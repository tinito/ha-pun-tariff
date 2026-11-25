from datetime import date, datetime

from pun_tariff_pure.bands import F1, F2, F3, band_at, band_of, easter_sunday, national_holidays
from pun_tariff_pure.gme import ROME


def test_weekday_bands():
    monday = date(2026, 11, 23)
    assert [band_of(monday, h) for h in (0, 6, 7, 8, 18, 19, 22, 23)] == [F3, F3, F2, F1, F1, F2, F2, F3]


def test_saturday_and_sunday():
    assert [band_of(date(2026, 11, 21), h) for h in (6, 7, 22, 23)] == [F3, F2, F2, F3]
    assert {band_of(date(2026, 11, 22), h) for h in range(24)} == {F3}


def test_holidays():
    assert easter_sunday(2026) == date(2026, 4, 5)
    holidays = national_holidays(2026)
    assert date(2026, 4, 6) in holidays          # Easter Monday
    assert date(2026, 10, 4) in holidays         # San Francesco from 2026
    assert date(2025, 10, 4) not in national_holidays(2025)
    assert band_of(date(2026, 12, 8), 10) == F3  # Immacolata on a Tuesday


def test_band_at_local_time():
    assert band_at(datetime(2026, 11, 25, 9, 30, tzinfo=ROME)) == F1
