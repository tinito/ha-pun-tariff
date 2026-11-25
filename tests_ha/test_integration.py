from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pun_tariff.const import CONF_CONTRACT, CONF_ENERGY_SENSOR, CONF_SOURCE, DOMAIN, SOURCE_WEB
from custom_components.pun_tariff.gme import ROME, PricePoint

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
