import base64
import io
import json
import zipfile
from datetime import datetime, timezone

from pun_tariff_pure.gme import interval_start, parse_api_content, parse_api_rows, parse_web_zip


def _web_zip(day="20261025", hours=25, quarters=100):
    hourly = "".join(f"<Prezzi><Data>{day}</Data><Mercato>MGP</Mercato><Ora>{h}</Ora><PUN>{100 + h},5</PUN></Prezzi>"
                     for h in range(1, hours + 1))
    quarter = "".join(f"<Prezzi15><Data>{day}</Data><Mercato>MGP</Mercato><PUN>{200 + q},25</PUN><Periodo>{q}</Periodo></Prezzi15>"
                      for q in range(1, quarters + 1))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{day}MGPPrezzi.xml", f"<NewDataSet>{hourly}</NewDataSet>")
        archive.writestr(f"{day}MGPPrezzi15.xml", f"<NewDataSet>{quarter}</NewDataSet>")
    return buffer.getvalue()


def test_interval_start_on_dst_day():
    # 25 October 2026: clocks go back, the day has 25 hours / 100 quarters
    assert interval_start(datetime(2026, 10, 25).date(), 1, 60) == datetime(2026, 10, 24, 22, tzinfo=timezone.utc)
    assert interval_start(datetime(2026, 10, 25).date(), 25, 60) == datetime(2026, 10, 25, 22, tzinfo=timezone.utc)
    assert interval_start(datetime(2026, 10, 25).date(), 100, 15) == datetime(2026, 10, 25, 22, 45, tzinfo=timezone.utc)


def test_parse_web_zip():
    points = parse_web_zip(_web_zip())
    hourly = [p for p in points if p.minutes == 60]
    quarters = [p for p in points if p.minutes == 15]
    assert len(hourly) == 25 and len(quarters) == 100
    assert hourly[0].price == 0.1015               # 101,5 EUR/MWh
    assert hourly[-1].local_start.hour == 23
    assert quarters[0].price == 0.20125


def test_parse_api_rows_keeps_pun_only():
    rows = [{"FlowDate": 20261124, "Hour": 9, "Period": "33", "Zone": "PUN", "Price": 250.0},
            {"FlowDate": 20261124, "Hour": 9, "Period": "33", "Zone": "NORD", "Price": 240.0}]
    [point] = parse_api_rows(rows, 15)
    assert point.minutes == 15 and point.price == 0.25
    assert point.local_start == datetime(2026, 11, 24, 8, 0, tzinfo=point.local_start.tzinfo)
    [hourly] = parse_api_rows(rows[:1], 60)
    assert hourly.local_start.hour == 8


def test_parse_api_content():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.json", json.dumps([{"FlowDate": 20261124, "Hour": 1, "Zone": "PUN", "Price": 100}]))
    [point] = parse_api_content(base64.b64encode(buffer.getvalue()).decode(), 60)
    assert point.price == 0.1
