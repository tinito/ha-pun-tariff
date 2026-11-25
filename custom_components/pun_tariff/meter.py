"""Month accounting: PUN averages, energy split by band and interval, interval costs.

Pure code (no Home Assistant): the state is a plain dict that the integration stores with
`homeassistant.helpers.storage.Store`.
"""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from statistics import fmean
from typing import Any

from .bands import BANDS, band_at
from .contract import Contract, Renderer, rates, template_variables
from .gme import ROME, PricePoint

MAX_DELTA_KWH = 50.0        # a larger jump between two readings is treated as a glitch
MAX_GAP = timedelta(days=2)  # readings further apart are not spread over the gap


def month_averages(points: Iterable[PricePoint], year: int, month: int) -> dict[str, float]:
    """Monthly PUN by band and overall: arithmetic mean of the hourly prices of the month."""
    by_band: dict[str, list[float]] = defaultdict(list)
    for point in points:
        local = point.local_start
        if point.minutes == 60 and local.year == year and local.month == month:
            by_band[band_at(local)].append(point.price)
    result = {band: fmean(values) for band, values in by_band.items()}
    hourly = [v for values in by_band.values() for v in values]
    if hourly:
        result["mono"] = fmean(hourly)
    result["hours"] = float(len(hourly))
    return result


class PriceBook:
    """PUN by interval start (UTC), per granularity."""

    def __init__(self) -> None:
        self._prices: dict[tuple[int, datetime], float] = {}

    def update(self, points: Iterable[PricePoint]) -> None:
        for point in points:
            self._prices[(point.minutes, point.start)] = point.price

    def points(self) -> list[PricePoint]:
        return [PricePoint(start, minutes, price) for (minutes, start), price in sorted(
            self._prices.items(), key=lambda item: item[0][1])]

    def price(self, start: datetime, minutes: int) -> float | None:
        return self._prices.get((minutes, start))

    def prune(self, before: datetime) -> None:
        for key in [k for k in self._prices if k[1] < before]:
            del self._prices[key]


def floor_interval(moment: datetime, minutes: int) -> datetime:
    """Start (UTC) of the interval containing `moment`. Italian offsets are whole hours."""
    utc = moment.astimezone(timezone.utc)
    return utc.replace(minute=utc.minute - utc.minute % minutes, second=0, microsecond=0)


def split_energy(kwh: float, start: datetime, end: datetime, minutes: int) -> list[tuple[datetime, float]]:
    """Spread `kwh` used between two readings over the intervals they cover, by time."""
    if end <= start or end - start > MAX_GAP:
        return [(floor_interval(end, minutes), kwh)]
    total = (end - start).total_seconds()
    parts = []
    cursor = start
    while cursor < end:
        interval = floor_interval(cursor, minutes)
        stop = min(end, interval + timedelta(minutes=minutes))
        parts.append((interval, kwh * (stop - cursor).total_seconds() / total))
        cursor = stop
    return parts


def new_month_state(year: int, month: int) -> dict[str, Any]:
    return {"year": year, "month": month, "kwh": {b: 0.0 for b in BANDS}, "costs": {}, "pending": {}}


class MonthMeter:
    """Accumulates the current month from the readings of a cumulative energy sensor."""

    def __init__(self, state: dict[str, Any] | None, contract: Contract, render: Renderer) -> None:
        self.contract = contract
        self.render = render
        self.state = state or {}
        self.state.setdefault("last_value", None)
        self.state.setdefault("last_time", None)
        self.state.setdefault("previous", None)
        if "current" not in self.state:
            now = datetime.now(ROME)
            self.state["current"] = new_month_state(now.year, now.month)
        self._rate_cache: dict[datetime, dict[str, float]] = {}

    @property
    def current(self) -> dict[str, Any]:
        return self.state["current"]

    def reading(self, value: float, moment: datetime, book: PriceBook,
                averages: Callable[[int, int], Mapping[str, float]]) -> float:
        """Record a new reading of the energy sensor; return the kWh accounted."""
        last_value, last_time = self.state["last_value"], self.state["last_time"]
        self.state["last_value"], self.state["last_time"] = value, moment.isoformat()
        if last_value is None:
            return 0.0
        delta = value - last_value
        if delta <= 0 or delta > MAX_DELTA_KWH:
            return 0.0  # counter reset, went back, or glitch
        start = datetime.fromisoformat(last_time) if last_time else moment
        minutes = self.contract.interval_minutes or 60
        for interval, kwh in split_energy(delta, start, moment, minutes):
            self._add(interval, minutes, kwh, book, averages)
        return delta

    def _add(self, interval: datetime, minutes: int, kwh: float, book: PriceBook,
             averages: Callable[[int, int], Mapping[str, float]]) -> None:
        local = interval.astimezone(ROME)
        month = self.current
        if (local.year, local.month) != (month["year"], month["month"]):
            return  # energy of another month: the caller rolls over before
        month["kwh"][band_at(local)] += kwh
        if self.contract.interval_minutes is None:
            return
        price = book.price(interval, minutes)
        if price is None:
            key = interval.isoformat()
            month["pending"][key] = month["pending"].get(key, 0.0) + kwh
            return
        self._price_energy(interval, price, kwh, averages)

    def _price_energy(self, interval: datetime, price: float, kwh: float,
                      averages: Callable[[int, int], Mapping[str, float]]) -> None:
        month = self.current
        interval_rates = self._rate_cache.get(interval)
        if interval_rates is None:
            local = interval.astimezone(ROME)
            variables = template_variables(
                self.contract, pun=price, band=band_at(local), averages=averages(local.year, local.month),
                kwh_month=sum(month["kwh"].values()), year=local.year, month=local.month,
                days_in_month=monthrange(local.year, local.month)[1])
            interval_rates = rates(self.contract, self.render, variables)
            self._rate_cache = {interval: interval_rates}  # one interval at a time is enough
        for name, rate in interval_rates.items():
            month["costs"][name] = month["costs"].get(name, 0.0) + kwh * rate

    def settle(self, book: PriceBook, averages: Callable[[int, int], Mapping[str, float]]) -> int:
        """Price the kWh of intervals whose PUN was missing; return how many were settled."""
        minutes = self.contract.interval_minutes
        if minutes is None:
            return 0
        pending = self.current["pending"]
        settled = 0
        for key in sorted(pending):
            interval = datetime.fromisoformat(key)
            price = book.price(interval, minutes)
            if price is not None:
                self._price_energy(interval, price, pending.pop(key), averages)
                settled += 1
        return settled

    def needs_rollover(self, now: datetime) -> bool:
        local = now.astimezone(ROME)
        return (local.year, local.month) != (self.current["year"], self.current["month"])

    def rollover(self, now: datetime, closed_bill: dict[str, Any]) -> None:
        """Start the month of `now`, keeping the closed month's bill as `previous`."""
        local = now.astimezone(ROME)
        self.state["previous"] = {"year": self.current["year"], "month": self.current["month"], **closed_bill}
        self.state["current"] = new_month_state(local.year, local.month)
        self._rate_cache = {}


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=ROME)
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=ROME)
    return start, end


def month_fraction(now: datetime, year: int, month: int) -> float:
    start, end = month_bounds(year, month)
    return min(1.0, max(0.0, (now - start).total_seconds() / (end - start).total_seconds()))
