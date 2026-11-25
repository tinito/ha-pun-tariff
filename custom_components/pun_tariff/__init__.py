"""PUN Tariff: what the electricity from the grid costs under an Italian PUN-indexed contract."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_CALCULATE_BILL
from .coordinator import PunTariffCoordinator

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type PunTariffConfigEntry = ConfigEntry[PunTariffCoordinator]

CALCULATE_BILL_SCHEMA = vol.Schema({
    vol.Optional("config_entry_id"): cv.string,
    vol.Required("month"): cv.matches_regex(r"^\d{4}-(0[1-9]|1[0-2])$"),
    vol.Optional("kwh_f1", default=0): vol.Coerce(float),
    vol.Optional("kwh_f2", default=0): vol.Coerce(float),
    vol.Optional("kwh_f3", default=0): vol.Coerce(float),
    vol.Optional("expected_total"): vol.Coerce(float),
})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's services."""

    async def calculate_bill(call: ServiceCall) -> ServiceResponse:
        entries = [e for e in hass.config_entries.async_loaded_entries(DOMAIN)
                   if call.data.get("config_entry_id") in (None, e.entry_id)]
        if len(entries) != 1:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="choose_entry")
        coordinator: PunTariffCoordinator = entries[0].runtime_data
        year, month = (int(part) for part in call.data["month"].split("-"))
        kwh = {"F1": call.data["kwh_f1"], "F2": call.data["kwh_f2"], "F3": call.data["kwh_f3"]}
        result = await coordinator.async_calculate_bill(year, month, kwh)
        if "expected_total" in call.data:
            result["expected_total"] = call.data["expected_total"]
            result["difference"] = round(result["total"] - call.data["expected_total"], 2)
        return result

    hass.services.async_register(DOMAIN, SERVICE_CALCULATE_BILL, calculate_bill, schema=CALCULATE_BILL_SCHEMA,
                                 supports_response=SupportsResponse.ONLY)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PunTariffConfigEntry) -> bool:
    coordinator = PunTariffCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PunTariffConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
