"""ARERA time bands (fasce orarie F1/F2/F3, delibera 181/06) and Italian national holidays.

F1: Monday-Friday 08-19
F2: Monday-Friday 07-08 and 19-23, Saturday 07-23
F3: Monday-Saturday 23-07, all Sunday and national holidays

The monthly PUN Index GME by band is the arithmetic mean of the hourly PUN of the hours in that
band (verified against the official July-October 2026 values to the fifth decimal).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache

F1, F2, F3 = "F1", "F2", "F3"
BANDS = (F1, F2, F3)


def easter_sunday(year: int) -> date:
    """Gregorian Easter (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=16)
def national_holidays(year: int) -> frozenset[date]:
    """Italian national holidays that put a whole day in F3."""
    days = {
        date(year, 1, 1),    # Capodanno
        date(year, 1, 6),    # Epifania
        easter_sunday(year) + timedelta(days=1),  # Lunedì dell'Angelo
        date(year, 4, 25),   # Liberazione
        date(year, 5, 1),    # Lavoro
        date(year, 6, 2),    # Repubblica
        date(year, 8, 15),   # Ferragosto
        date(year, 11, 1),   # Ognissanti
        date(year, 12, 8),   # Immacolata
        date(year, 12, 25),  # Natale
        date(year, 12, 26),  # Santo Stefano
    }
    if year >= 2026:
        days.add(date(year, 10, 4))  # San Francesco, national holiday again from 2026
    return frozenset(days)


def band_of(day: date, hour: int) -> str:
    """Band of the hour starting at local `hour` (0-23) of `day`."""
    if day in national_holidays(day.year) or day.weekday() == 6:
        return F3
    if day.weekday() == 5:
        return F2 if 7 <= hour < 23 else F3
    if 8 <= hour < 19:
        return F1
    if hour == 7 or 19 <= hour < 23:
        return F2
    return F3


def band_at(moment: datetime) -> str:
    """Band at a local, timezone-aware moment."""
    return band_of(moment.date(), moment.hour)
