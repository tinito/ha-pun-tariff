from datetime import datetime, timedelta, timezone

import pytest

from pun_tariff_pure.contract import parse_contract
from pun_tariff_pure.gme import ROME, PricePoint
from pun_tariff_pure.meter import MonthMeter, PriceBook, month_averages, month_fraction, split_energy

QUARTER = parse_contract({"pun": "quarter_hourly", "per_kwh": {"energy": "{{ pun * 1.10 }}", "network": 0.05},
                          "vat": 0.10})
BANDS_CONTRACT = parse_contract({"pun": "monthly_bands", "per_kwh": {"energy": "{{ pun }}"}, "vat": 0})


def no_averages(year, month):
    return {}


def _utc(*args):
    return datetime(*args, tzinfo=ROME).astimezone(timezone.utc)


def test_month_averages_by_band():
    # Monday 2 November 2026: 08-19 F1, 07 and 19-22 F2, the rest F3
    start = _utc(2026, 11, 2, 0)
    points = [PricePoint(start + timedelta(hours=h), 60, 0.1 if 8 <= h < 19 else 0.2) for h in range(24)]
    averages = month_averages(points, 2026, 11)
    assert averages["F1"] == pytest.approx(0.1)
    assert averages["F2"] == pytest.approx(0.2)
    assert averages["hours"] == 24


def test_split_energy_over_quarters():
    parts = split_energy(1.0, _utc(2026, 11, 2, 8, 10), _utc(2026, 11, 2, 8, 40), 15)
    assert [round(kwh, 4) for _, kwh in parts] == [0.1667, 0.5, 0.3333]


def test_quarter_hour_contract_prices_each_quarter(render):
    book = PriceBook()
    book.update([PricePoint(_utc(2026, 11, 2, 8, 0), 15, 0.2), PricePoint(_utc(2026, 11, 2, 8, 15), 15, 0.4)])
    meter = MonthMeter(None, QUARTER, render)
    meter.state["current"].update(year=2026, month=11)
    meter.reading(100.0, _utc(2026, 11, 2, 8, 0), book, no_averages)
    meter.reading(101.0, _utc(2026, 11, 2, 8, 15), book, no_averages)   # 1 kWh in the 08:00 quarter
    meter.reading(103.0, _utc(2026, 11, 2, 8, 30), book, no_averages)   # 2 kWh in the 08:15 quarter
    costs = meter.current["costs"]
    assert costs["energy"] == pytest.approx(1 * 0.22 + 2 * 0.44)
    assert costs["network"] == pytest.approx(0.15)
    assert meter.current["kwh"]["F1"] == pytest.approx(3)


def test_missing_price_is_settled_later(render):
    book = PriceBook()
    meter = MonthMeter(None, QUARTER, render)
    meter.state["current"].update(year=2026, month=11)
    meter.reading(10.0, _utc(2026, 11, 2, 9, 0), book, no_averages)
    meter.reading(11.0, _utc(2026, 11, 2, 9, 14), book, no_averages)
    assert meter.current["pending"] and "energy" not in meter.current["costs"]
    book.update([PricePoint(_utc(2026, 11, 2, 9, 0), 15, 0.3)])
    assert meter.settle(book, no_averages) == 1
    assert meter.current["costs"]["energy"] == pytest.approx(0.33)


def test_counter_going_back_is_ignored(render):
    meter = MonthMeter(None, BANDS_CONTRACT, render)
    meter.state["current"].update(year=2026, month=11)
    book = PriceBook()
    meter.reading(25.36, _utc(2026, 11, 8, 8, 0), book, no_averages)
    assert meter.reading(21.56, _utc(2026, 11, 8, 8, 5), book, no_averages) == 0
    assert meter.reading(21.60, _utc(2026, 11, 8, 8, 10), book, no_averages) == pytest.approx(0.04)


def test_rollover_keeps_previous_month(render):
    meter = MonthMeter(None, BANDS_CONTRACT, render)
    meter.state["current"].update(year=2026, month=11)
    assert meter.needs_rollover(_utc(2026, 12, 1, 0, 1))
    meter.rollover(_utc(2026, 12, 1, 0, 1), {"total": 12.3})
    assert meter.state["previous"] == {"year": 2026, "month": 11, "total": 12.3}
    assert meter.current["month"] == 12


def test_month_fraction():
    assert month_fraction(datetime(2026, 11, 16, tzinfo=ROME), 2026, 11) == pytest.approx(0.5)
