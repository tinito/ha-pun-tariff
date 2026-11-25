"""Sensors: price now, month cost and energy, previous month, PUN, current band."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (SensorDeviceClass, SensorEntity, SensorEntityDescription,
                                             SensorStateClass)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PunTariffConfigEntry
from .bands import BANDS
from .const import DOMAIN
from .coordinator import PunTariffCoordinator
from .gme import SOURCE_NAME

PRICE_UNIT = "EUR/kWh"


@dataclass(frozen=True, kw_only=True)
class PunTariffSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]
    attributes: Callable[[dict[str, Any]], dict[str, Any] | None] = lambda data: None
    reset: bool = False          # state_class total that restarts every month
    always_available: bool = False


def _bill_value(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda data: (data.get("bill") or {}).get(key)


def _breakdown(bill: dict[str, Any] | None) -> dict[str, Any] | None:
    if not bill:
        return None
    return {key: bill[key] for key in ("kwh_by_band", "per_kwh", "per_month", "taxable", "vat") if key in bill}


SENSORS: tuple[PunTariffSensorDescription, ...] = (
    PunTariffSensorDescription(
        key="price", translation_key="price", native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=4,
        value=lambda data: data.get("price")),
    PunTariffSensorDescription(
        key="month_cost", translation_key="month_cost", native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY, state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2, reset=True, always_available=True,
        value=_bill_value("total"), attributes=lambda data: _breakdown(data.get("bill"))),
    PunTariffSensorDescription(
        key="month_energy", translation_key="month_energy", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2, reset=True, always_available=True,
        value=_bill_value("kwh"), attributes=lambda data: {"kwh_by_band": (data.get("bill") or {}).get("kwh_by_band")}),
    PunTariffSensorDescription(
        key="previous_month_cost", translation_key="previous_month_cost", native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY, suggested_display_precision=2, always_available=True,
        value=lambda data: (data.get("previous") or {}).get("total"),
        attributes=lambda data: (None if not data.get("previous") else {
            "month": f"{data['previous']['year']}-{data['previous']['month']:02d}",
            "kwh": data["previous"].get("kwh"), **(_breakdown(data["previous"]) or {})})),
    PunTariffSensorDescription(
        key="pun", translation_key="pun", native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=5,
        value=lambda data: data.get("pun")),
    *(PunTariffSensorDescription(
        key=f"pun_month_{band.lower()}", translation_key=f"pun_month_{band.lower()}",
        native_unit_of_measurement=PRICE_UNIT, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=5, value=lambda data, band=band: (data.get("averages") or {}).get(band))
      for band in (*BANDS, "mono")),
    PunTariffSensorDescription(
        key="band", translation_key="band", device_class=SensorDeviceClass.ENUM, options=list(BANDS),
        value=lambda data: data.get("band")),
)


async def async_setup_entry(hass: HomeAssistant, entry: PunTariffConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(PunTariffSensor(entry.runtime_data, description) for description in SENSORS)


class PunTariffSensor(CoordinatorEntity[PunTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_attribution = f"PUN Index GME: {SOURCE_NAME}"
    entity_description: PunTariffSensorDescription

    def __init__(self, coordinator: PunTariffCoordinator, description: PunTariffSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
                                            entry_type=DeviceEntryType.SERVICE, manufacturer="GME / ARERA")

    @property
    def available(self) -> bool:
        if self.entity_description.always_available:
            return self.coordinator.data is not None
        return super().available

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        return self.entity_description.value(data)

    @property
    def last_reset(self) -> datetime | None:
        if not self.entity_description.reset:
            return None
        return (self.coordinator.data or {}).get("month_start")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attributes(self.coordinator.data or {})
