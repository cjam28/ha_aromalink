"""Shared entity base for the Aroma-Link integration.

unique_id doctrine: ``{username}_{device_id}_{suffix}`` — the exact strings
v2.x used, so surviving entities keep their entity_ids across the v3 upgrade.
Never normalize these.
"""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class AromaLinkEntity(CoordinatorEntity):
    """Base entity bound to a device coordinator with the legacy unique_id scheme."""

    _attr_has_entity_name = False

    def __init__(self, coordinator, entry, device_id, device_name, suffix, name_suffix):
        super().__init__(coordinator)
        self._entry = entry
        self._device_id = device_id
        self._attr_name = f"{device_name} {name_suffix}"
        self._attr_unique_id = f"{entry.data['username']}_{device_id}_{suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.data['username']}_{self._device_id}")},
            name=self.coordinator.device_name,
            manufacturer="Aroma-Link",
            model="Diffuser",
        )
