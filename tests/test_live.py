"""Download from the real GME website (opt-in: PUN_TARIFF_LIVE=1, needs aiohttp).

Checks that the undocumented download still works and gives the official monthly PUN by band.
"""
import asyncio
import os
from datetime import date

import pytest

from pun_tariff_pure.gme import GmeWebClient
from pun_tariff_pure.meter import month_averages

pytestmark = pytest.mark.skipif(os.environ.get("PUN_TARIFF_LIVE") != "1", reason="set PUN_TARIFF_LIVE=1")


def test_website_october_2026():
    aiohttp = pytest.importorskip("aiohttp")

    async def run(func, *args):
        return func(*args)

    async def fetch():
        async with aiohttp.ClientSession() as session:
            return await GmeWebClient(session, run).fetch(date(2026, 10, 1), date(2026, 10, 31))

    points = asyncio.run(fetch())
    assert sum(p.minutes == 60 for p in points) == 745        # 25 October has 25 hours
    assert sum(p.minutes == 15 for p in points) == 745 * 4
    averages = month_averages(points, 2026, 10)
    assert {band: round(averages[band], 5) for band in ("F1", "F2", "F3")} == \
        {"F1": 0.23339, "F2": 0.25214, "F3": 0.21588}
