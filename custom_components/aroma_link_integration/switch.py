"""Switch platform for Aroma-Link.

v3 surface: Power and Fan drive the device directly (shielded command paths);
Schedule Enabled and Night Owl are the persisted gating-engine master flags
(store-backed — they never write device schedule slots).
"""
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback

from .const import DOMAIN, EVENT_UPDATED
from .entity import AromaLinkEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Aroma-Link switches based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators = data["device_coordinators"]
    store = data["store"]

    entities = []
    for device_id, coordinator in device_coordinators.items():
        device_name = coordinator.device_name
        entities.append(AromaLinkPowerSwitch(coordinator, entry, device_id, device_name))
        entities.append(AromaLinkFanSwitch(coordinator, entry, device_id, device_name))
        entities.append(
            AromaLinkFlagSwitch(
                coordinator, entry, device_id, device_name, store,
                suffix="schedule_active",
                name_suffix="Schedule Enabled",
                flag="schedule_enabled",
                icon="mdi:calendar-check",
            )
        )
        entities.append(
            AromaLinkFlagSwitch(
                coordinator, entry, device_id, device_name, store,
                suffix="night_owl",
                name_suffix="Night Owl",
                flag="night_owl_enabled",
                icon="mdi:owl",
            )
        )

    async_add_entities(entities)


class AromaLinkPowerSwitch(AromaLinkEntity, SwitchEntity):
    """Device power. The gating engine may correct manual flips."""

    def __init__(self, coordinator, entry, device_id, device_name):
        super().__init__(coordinator, entry, device_id, device_name, "switch", "Power")

    @property
    def is_on(self):
        return self.coordinator.data.get("state", False)

    async def async_turn_on(self, **kwargs):
        await self.coordinator.turn_on_off(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.turn_on_off(False)


class AromaLinkFanSwitch(AromaLinkEntity, SwitchEntity):
    """Exhaust fan."""

    def __init__(self, coordinator, entry, device_id, device_name):
        super().__init__(coordinator, entry, device_id, device_name, "fan", "Fan")
        self._attr_icon = "mdi:fan"

    @property
    def is_on(self):
        return self.coordinator.data.get("fan_state", False)

    async def async_turn_on(self, **kwargs):
        await self.coordinator.fan_control(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.fan_control(False)


class AromaLinkFlagSwitch(AromaLinkEntity, SwitchEntity):
    """Persisted master flag (schedule_enabled / night_owl_enabled)."""

    def __init__(
        self, coordinator, entry, device_id, device_name, store,
        suffix, name_suffix, flag, icon,
    ):
        super().__init__(coordinator, entry, device_id, device_name, suffix, name_suffix)
        self._store = store
        self._flag = flag
        self._attr_icon = icon

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        @callback
        def _on_updated(event):
            if event.data.get("device_id") == str(self._device_id) and event.data.get(
                "change"
            ) in ("flags", "schedule"):
                self.async_write_ha_state()

        self.async_on_remove(self.hass.bus.async_listen(EVENT_UPDATED, _on_updated))

    @property
    def is_on(self):
        return getattr(self._store.get_model(self._device_id), self._flag)

    async def _set(self, value: bool):
        await self._store.async_set_flags(self._device_id, **{self._flag: value})
        entry_data = self.hass.data[DOMAIN][self._entry.entry_id]
        reconciler = (entry_data.get("reconcilers") or {}).get(str(self._device_id))
        if reconciler and self._flag == "night_owl_enabled":
            reconciler.async_request_sync("flag_switch")

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)
