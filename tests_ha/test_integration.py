from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from custom_components.pun_tariff.const import (CONF_CONTRACT, CONF_ENERGY_SENSOR, CONF_SOURCE, DOMAIN, SOURCE_API,
                                               SOURCE_WEB)
from custom_components.pun_tariff.gme import ROME, GmeAuthError, GmeError, PricePoint

CONTRACT = {"name": "Test", "pun": "quarter_hourly", "per_kwh": {"energy": "{{ pun * 1.1 }}", "network": 0.05},
            "per_month": {"fixed": 10}, "vat": 0.1}


def _prices(start: date, end: date):
    points = []
    day = start
    while day <= end:
        midnight = datetime(day.year, day.month, day.day, tzinfo=ROME).astimezone(timezone.utc)
        points += [PricePoint(midnight + timedelta(hours=h), 60, 0.2) for h in range(24)]
        points += [PricePoint(midnight + timedelta(minutes=15 * q), 15, 0.2) for q in range(96)]
        day += timedelta(days=1)
    return points


async def _fake_fetch(self, start, end, *args):
    return _prices(start, end)


@pytest.fixture
def web_prices():
    with patch("custom_components.pun_tariff.gme.GmeWebClient.fetch", _fake_fetch):
        yield


async def test_config_flow_web(hass: HomeAssistant, web_prices):
    hass.states.async_set("sensor.grid", "100", {"unit_of_measurement": "kWh", "device_class": "energy"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_SOURCE: SOURCE_WEB})
    assert result["step_id"] == "contract"
    bad = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENERGY_SENSOR: "sensor.grid", CONF_CONTRACT: {"pun": "weekly"}})
    assert bad["errors"] == {CONF_CONTRACT: "invalid_contract"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENERGY_SENSOR: "sensor.grid", CONF_CONTRACT: CONTRACT})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test"


async def test_setup_sensors_and_costs(hass: HomeAssistant, web_prices):
    hass.states.async_set("sensor.grid", "100", {"unit_of_measurement": "kWh"})
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_SOURCE: SOURCE_WEB},
                            options={CONF_ENERGY_SENSOR: "sensor.grid", CONF_CONTRACT: CONTRACT}, title="Test")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    states = {s.entity_id: s for s in hass.states.async_all("sensor") if s.entity_id != "sensor.grid"}
    price = next(s for s in states.values() if s.attributes.get("unit_of_measurement") == "EUR/kWh"
                 and "price" in s.entity_id)
    assert float(price.state) == pytest.approx((0.2 * 1.1 + 0.05) * 1.1)

    hass.states.async_set("sensor.grid", "101", {"unit_of_measurement": "kWh"})   # first reading is the baseline
    hass.states.async_set("sensor.grid", "103", {"unit_of_measurement": "kWh"})   # +2 kWh
    await hass.async_block_till_done()
    energy = next(s for s in states if "energy" in s)
    assert float(hass.states.get(energy).state) == pytest.approx(2.0)
    cost = hass.states.get(next(s for s in states if "cost" in s and "last" not in s and "scorso" not in s))
    assert cost.attributes["per_kwh"]["energy"] == pytest.approx(2 * 0.22)

    response = await hass.services.async_call(
        DOMAIN, "calculate_bill", {"month": "2026-10", "kwh_f1": 100, "expected_total": 40},
        blocking=True, return_response=True)
    assert response["total"] == pytest.approx((100 * (0.22 + 0.05) + 10) * 1.1)
    assert response["approximated"] is True
    assert "difference" in response

    assert await hass.config_entries.async_unload(entry.entry_id)


async def _setup(hass, data):
    hass.states.async_set("sensor.grid", "100", {"unit_of_measurement": "kWh"})
    entry = MockConfigEntry(domain=DOMAIN, data=data, title="Test",
                            options={CONF_ENERGY_SENSOR: "sensor.grid", CONF_CONTRACT: CONTRACT})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


API = {CONF_SOURCE: SOURCE_API, CONF_USERNAME: "user", CONF_PASSWORD: "secret"}


def _raise(error):
    async def fetch(self, *args):
        raise error
    return fetch


@pytest.mark.parametrize("error", [GmeError("GME API answered 503"), TimeoutError()])
async def test_api_failure_falls_back_to_the_site(hass: HomeAssistant, web_prices, error):
    with patch("custom_components.pun_tariff.gme.GmeApiClient.fetch", _raise(error)):
        entry = await _setup(hass, API)
    pun = hass.states.get("sensor.test_pun_now")
    assert float(pun.state) == pytest.approx(0.2)
    assert pun.attributes["source"] == SOURCE_WEB
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert entry.state.name == "LOADED"


async def test_rejected_credentials_ask_for_new_ones(hass: HomeAssistant, web_prices):
    with patch("custom_components.pun_tariff.gme.GmeApiClient.fetch", _raise(GmeAuthError("wrong password"))):
        entry = await _setup(hass, API)
    assert float(hass.states.get("sensor.test_pun_now").state) == pytest.approx(0.2)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    assert entry.state.name == "LOADED"


async def test_api_source(hass: HomeAssistant):
    async def api_fetch(self, start, end, granularity):
        return [p for p in _prices(start, end) if p.minutes == (60 if granularity == "PT60" else 15)]
    with patch("custom_components.pun_tariff.gme.GmeApiClient.fetch", api_fetch), \
            patch("custom_components.pun_tariff.gme.GmeWebClient.fetch", _raise(AssertionError("site used"))):
        await _setup(hass, API)
    assert hass.states.get("sensor.test_pun_now").attributes["source"] == SOURCE_API


async def test_reconfigure_from_site_to_api_and_back(hass: HomeAssistant, web_prices):
    entry = await _setup(hass, {CONF_SOURCE: SOURCE_WEB})
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_SOURCE: SOURCE_API})
    assert result["step_id"] == "reconfigure_credentials"
    with patch("custom_components.pun_tariff.gme.GmeApiClient.authenticate", _raise(GmeAuthError("no"))):
        bad = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "user", CONF_PASSWORD: "wrong"})
    assert bad["errors"] == {"base": "invalid_auth"}

    async def ok(self):
        return None
    with patch("custom_components.pun_tariff.gme.GmeApiClient.authenticate", ok), \
            patch("custom_components.pun_tariff.gme.GmeApiClient.fetch", _raise(GmeError("down"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "user", CONF_PASSWORD: "secret"})
        await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == API
    assert entry.options[CONF_CONTRACT] == CONTRACT

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_SOURCE: SOURCE_WEB})
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_SOURCE: SOURCE_WEB}


async def test_options_change_reloads(hass: HomeAssistant, web_prices):
    entry = await _setup(hass, {CONF_SOURCE: SOURCE_WEB})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ENERGY_SENSOR: "sensor.grid", CONF_CONTRACT: {**CONTRACT, "per_month": {"fixed": 0}}})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.contract.per_month == {"fixed": 0}
