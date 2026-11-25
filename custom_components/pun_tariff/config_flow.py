"""Config flow: price source, GME API credentials, energy sensor and contract (YAML)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (EntitySelector, EntitySelectorConfig, ObjectSelector, SelectSelector,
                                            SelectSelectorConfig, SelectSelectorMode, TextSelector,
                                            TextSelectorConfig, TextSelectorType)

from .const import CONF_CONTRACT, CONF_ENERGY_SENSOR, CONF_SOURCE, DOMAIN, SOURCE_API, SOURCE_WEB
from .contract import ContractError
from .coordinator import check_contract
from .gme import GmeApiClient, GmeAuthError, GmeError

EXAMPLE_CONTRACT = {
    "name": "My contract",
    "pun": "monthly_bands",
    "vars": {"spread": 0.018, "losses": 0.10},
    "per_kwh": {"energy": "{{ (pun + spread) * (1 + losses) }}", "network": 0.0479, "excise": 0.0227},
    "per_month": {"supplier": 6.0, "network_fixed": 1.92, "power": "{{ 1.975556 * 3 }}"},
    "vat": 0.10,
}


def _credentials_schema(username: str = "") -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_USERNAME, default=username): str,
        vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    })


def _contract_schema(sensor: str | None, contract: Mapping[str, Any]) -> vol.Schema:
    sensor_key = vol.Required(CONF_ENERGY_SENSOR, default=sensor) if sensor else vol.Required(CONF_ENERGY_SENSOR)
    return vol.Schema({
        sensor_key: EntitySelector(EntitySelectorConfig(domain="sensor", device_class="energy")),
        vol.Required(CONF_CONTRACT, default=dict(contract)): ObjectSelector(),
    })


async def _check_credentials(hass, username: str, password: str) -> str | None:
    client = GmeApiClient(async_get_clientsession(hass), hass.async_add_executor_job, username, password)
    try:
        await client.authenticate()
    except GmeAuthError:
        return "invalid_auth"
    except (GmeError, TimeoutError, OSError):
        return "cannot_connect"
    return None


class PunTariffConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_SOURCE] = user_input[CONF_SOURCE]
            if user_input[CONF_SOURCE] == SOURCE_API:
                return await self.async_step_credentials()
            return await self.async_step_contract()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_SOURCE, default=SOURCE_API): SelectSelector(SelectSelectorConfig(
                options=[SOURCE_API, SOURCE_WEB], translation_key=CONF_SOURCE, mode=SelectSelectorMode.LIST)),
        }))

    async def async_step_credentials(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _check_credentials(self.hass, user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if error is None:
                self._data.update(user_input)
                return await self.async_step_contract()
            errors["base"] = error
        return self.async_show_form(step_id="credentials", data_schema=_credentials_schema(), errors=errors)

    async def async_step_contract(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            try:
                contract = check_contract(self.hass, user_input[CONF_CONTRACT])
            except ContractError as err:
                errors[CONF_CONTRACT] = "invalid_contract"
                placeholders["error"] = str(err)
            else:
                return self.async_create_entry(title=contract.name or "PUN Tariff", data=self._data,
                                               options=dict(user_input))
        return self.async_show_form(
            step_id="contract", errors=errors, description_placeholders=placeholders,
            data_schema=_contract_schema((user_input or {}).get(CONF_ENERGY_SENSOR),
                                         (user_input or {}).get(CONF_CONTRACT) or EXAMPLE_CONTRACT))

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _check_credentials(self.hass, user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if error is None:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
            errors["base"] = error
        return self.async_show_form(step_id="reauth_confirm", errors=errors,
                                    data_schema=_credentials_schema(entry.data.get(CONF_USERNAME, "")))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PunTariffOptionsFlow()


class PunTariffOptionsFlow(OptionsFlow):
    """Change the energy sensor or the contract."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            try:
                check_contract(self.hass, user_input[CONF_CONTRACT])
            except ContractError as err:
                errors[CONF_CONTRACT] = "invalid_contract"
                placeholders["error"] = str(err)
            else:
                return self.async_create_entry(data=dict(user_input))
        current = {**self.config_entry.options, **(user_input or {})}
        return self.async_show_form(step_id="init", errors=errors, description_placeholders=placeholders,
                                    data_schema=_contract_schema(current.get(CONF_ENERGY_SENSOR),
                                                                 current.get(CONF_CONTRACT) or EXAMPLE_CONTRACT))
