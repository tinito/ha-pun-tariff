"""Coordinator: PUN prices from GME, energy readings, and the month's bill."""
from __future__ import annotations

import logging
from calendar import monthrange
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, TemplateError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.template import Template
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .bands import band_at
from .const import (CONF_CONTRACT, CONF_ENERGY_SENSOR, CONF_SOURCE, DAY_AHEAD_HOUR, DOMAIN,
                    SOURCE_API, STORAGE_VERSION, UPDATE_INTERVAL)
from .contract import MONTHLY_BANDS, Contract, ContractError, bill, parse_contract, rates, template_variables
from .gme import ROME, GmeApiClient, GmeAuthError, GmeError, GmeWebClient
from .meter import MonthMeter, PriceBook, month_averages, month_bounds, month_fraction

_LOGGER = logging.getLogger(__name__)


class TemplateRenderer:
    """Render contract values with Home Assistant templates, caching the compiled templates."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._templates: dict[str, Template] = {}

    def __call__(self, value: Any, variables: Mapping[str, Any]) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        template = self._templates.get(value)
        if template is None:
            template = self._templates[value] = Template(value, self._hass)
        try:
            return float(template.async_render(dict(variables), parse_result=False))
        except (TemplateError, ValueError, TypeError) as err:
            raise ContractError(f"{value}: {err}") from err


def check_contract(hass: HomeAssistant, data: Any) -> Contract:
    """Parse a contract and render every value once with sample variables."""
    contract = parse_contract(data)
    render = TemplateRenderer(hass)
    sample = template_variables(contract, pun=0.12, band="F1", averages={"F1": 0.12, "F2": 0.11, "F3": 0.1, "mono": 0.11},
                                kwh_month=100.0, year=2026, month=1, days_in_month=31)
    bill(contract, render, year=2026, month=1, days_in_month=31, kwh_by_band={"F1": 1.0},
         averages={"F1": 0.12, "F2": 0.11, "F3": 0.1, "mono": 0.11})
    rates(contract, render, sample)
    return contract


class PunTariffCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keeps the PUN prices and the month's accounting up to date."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        options = {**entry.data, **entry.options}
        self.energy_sensor: str = options[CONF_ENERGY_SENSOR]
        self.render = TemplateRenderer(hass)
        self.contract = parse_contract(options[CONF_CONTRACT])
        session = async_get_clientsession(hass)
        if options.get(CONF_SOURCE) == SOURCE_API:
            self.client: GmeApiClient | GmeWebClient = GmeApiClient(
                session, hass.async_add_executor_job, options[CONF_USERNAME], options[CONF_PASSWORD])
        else:
            self.client = GmeWebClient(session, hass.async_add_executor_job)
        self.book = PriceBook()
        self.meter: MonthMeter | None = None
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._fetched: tuple[date, bool] | None = None   # (day, had tomorrow's prices)
        self._averages_cache: dict[tuple[int, int], dict[str, float]] = {}

    async def async_setup(self) -> None:
        """Restore the month state and start listening to the energy sensor and the clock."""
        self.meter = MonthMeter(await self._store.async_load(), self.contract, self.render)
        entry = self.config_entry
        entry.async_on_unload(async_track_state_change_event(self.hass, [self.energy_sensor], self._on_energy))
        entry.async_on_unload(async_track_time_change(self.hass, self._on_quarter, minute=[0, 15, 30, 45], second=2))

    # --- prices -------------------------------------------------------------------------------

    def averages(self, year: int, month: int) -> dict[str, float]:
        key = (year, month)
        if key not in self._averages_cache:
            self._averages_cache[key] = month_averages(self.book.points(), year, month)
        return self._averages_cache[key]

    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.now(ROME)
        tomorrow = now.date() + timedelta(days=1)
        has_tomorrow = self.book.price(datetime.combine(tomorrow, datetime.min.time(), ROME).astimezone(dt_util.UTC), 60) is not None
        stale = self._fetched is None or self._fetched[0] != now.date() or (
            now.hour >= DAY_AHEAD_HOUR and not has_tomorrow)
        if stale and await self._fetch_prices(now, tomorrow):
            self._fetched = (now.date(), has_tomorrow)
        return self._compute()

    async def async_shutdown(self) -> None:
        """Write the month state before unloading."""
        await super().async_shutdown()
        if self.meter is not None:
            await self._store.async_save(self.meter.state)

    async def _fetch_prices(self, now: datetime, tomorrow: date) -> bool:
        """Download the PUN from the start of the month to tomorrow; False if it failed."""
        first = date(now.year, now.month, 1)
        if now.day <= 2:  # the closed month must be complete for its final bill
            first = (first - timedelta(days=1)).replace(day=1)
        try:
            if isinstance(self.client, GmeApiClient):
                self.book.update(await self.client.fetch(first, tomorrow, "PT60"))
                if self.contract.pun == "quarter_hourly":
                    self.book.update(await self.client.fetch(now.date(), tomorrow, "PT15"))
            else:
                self.book.update(await self.client.fetch(first, tomorrow))
        except GmeAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (GmeError, TimeoutError, OSError) as err:
            if not self.book.points():
                raise UpdateFailed(f"GME prices not available: {err}") from err
            _LOGGER.warning("GME prices not updated, keeping the last ones: %s", err)
            return False
        self.book.prune(datetime.combine(first, datetime.min.time(), ROME).astimezone(dt_util.UTC))
        self._averages_cache.clear()
        if self.meter and self.meter.settle(self.book, self.averages):
            self._save()
        return True

    # --- energy -------------------------------------------------------------------------------

    @callback
    def _on_energy(self, event: Event[EventStateChangedData]) -> None:
        state = event.data["new_state"]
        if self.meter is None or state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        try:
            value = float(state.state)
        except ValueError:
            return
        if state.attributes.get("unit_of_measurement") == "Wh":
            value /= 1000
        moment = state.last_updated
        self._rollover_if_needed(moment)
        try:
            self.meter.reading(value, moment, self.book, self.averages)
        except ContractError as err:
            _LOGGER.error("Contract template error: %s", err)
            return
        self._save()
        self.async_set_updated_data(self._compute())

    @callback
    def _on_quarter(self, now: datetime) -> None:
        if self.meter is None:
            return
        self._rollover_if_needed(now)
        self.async_set_updated_data(self._compute())

    def _rollover_if_needed(self, moment: datetime) -> None:
        assert self.meter is not None
        if not self.meter.needs_rollover(moment):
            return
        self.meter.settle(self.book, self.averages)
        closed = self.meter.current
        self.meter.rollover(moment, self._bill(closed, final=True))
        self._save()

    def _save(self) -> None:
        assert self.meter is not None
        self._store.async_delay_save(lambda: self.meter.state, 30)

    # --- bill -----------------------------------------------------------------------------------

    def _bill(self, month: Mapping[str, Any], *, final: bool) -> dict[str, Any]:
        year, number = month["year"], month["month"]
        fraction = 1.0 if final else month_fraction(dt_util.now(ROME), year, number)
        return bill(self.contract, self.render, year=year, month=number, days_in_month=monthrange(year, number)[1],
                    kwh_by_band=month["kwh"], averages=self.averages(year, number),
                    interval_costs=month["costs"], month_fraction=fraction)

    def _compute(self) -> dict[str, Any]:
        now = dt_util.now(ROME)
        averages = self.averages(now.year, now.month)
        band = band_at(now)
        minutes = self.contract.interval_minutes
        utc = now.astimezone(dt_util.UTC)
        pun_now = None
        for granularity in ((minutes,) if minutes else ()) + (15, 60):
            start = utc.replace(minute=utc.minute - utc.minute % granularity, second=0, microsecond=0)
            pun_now = self.book.price(start, granularity)
            if pun_now is not None:
                break
        data: dict[str, Any] = {"band": band, "pun": pun_now, "averages": averages, "price": None,
                                "bill": None, "previous": None, "month_start": month_bounds(now.year, now.month)[0]}
        try:
            applied = pun_now if minutes else averages.get(band if self.contract.pun == MONTHLY_BANDS else "mono")
            if applied is not None and self.meter is not None:
                variables = template_variables(
                    self.contract, pun=applied, band=band, averages=averages,
                    kwh_month=sum(self.meter.current["kwh"].values()), year=now.year, month=now.month,
                    days_in_month=monthrange(now.year, now.month)[1])
                data["price"] = sum(rates(self.contract, self.render, variables).values()) * (1 + self.contract.vat)
            if self.meter is not None:
                data["bill"] = self._bill(self.meter.current, final=False)
                data["previous"] = self.meter.state.get("previous")
        except ContractError as err:
            _LOGGER.error("Contract template error: %s", err)
        return data

    async def async_calculate_bill(self, year: int, month: int, kwh_by_band: Mapping[str, float]) -> dict[str, Any]:
        """Bill of a past month from its kWh by band (for checking the contract against an invoice)."""
        first, end = date(year, month, 1), month_bounds(year, month)[1].date() - timedelta(days=1)
        if self.averages(year, month).get("hours", 0) < monthrange(year, month)[1] * 23:
            if isinstance(self.client, GmeApiClient):
                points = await self.client.fetch(first, end, "PT60")
            else:
                points = await self.client.fetch(first, end)
            self._averages_cache[(year, month)] = month_averages(points, year, month)
        averages = self.averages(year, month)
        contract = self.contract
        if contract.interval_minutes is not None:
            # interval contracts need the consumption of every interval: approximate with band averages
            contract = Contract(pun=MONTHLY_BANDS, per_kwh=contract.per_kwh, per_month=contract.per_month,
                                vat=contract.vat, name=contract.name, vars=contract.vars)
        result = bill(contract, self.render, year=year, month=month, days_in_month=monthrange(year, month)[1],
                      kwh_by_band=kwh_by_band, averages=averages)
        result["pun"] = {k: round(v, 5) for k, v in averages.items() if k != "hours"}
        result["approximated"] = contract is not self.contract
        return result
