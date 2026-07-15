"""Button platform for Aroma-Link.

v3 keeps one everyday button: Refill (keep calibration). The calibration
workflow moved to the ``oil_calibrate``/``oil_refill`` services driven from
the card.
"""
from homeassistant.components.button import ButtonEntity

from .const import DOMAIN
from .entity import AromaLinkEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Aroma-Link buttons based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators = data["device_coordinators"]

    entities = [
        AromaLinkRefillButton(coordinator, entry, device_id, coordinator.device_name)
        for device_id, coordinator in device_coordinators.items()
    ]
    async_add_entities(entities)


class AromaLinkRefillButton(AromaLinkEntity, ButtonEntity):
    """Record an oil refill without resetting calibration."""

    _attr_icon = "mdi:water-plus-outline"

    def __init__(self, coordinator, entry, device_id, device_name):
        super().__init__(
            coordinator, entry, device_id, device_name,
            "oil_refill_keep_calibration", "Refill",
        )

    async def async_press(self):
        self.coordinator.refill_keep_calibration()
        self.coordinator.async_update_listeners()
