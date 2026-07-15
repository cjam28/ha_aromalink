"""Number platform for Aroma-Link.

Work/Pause Duration are the persisted DEFAULTS used for timed runs and newly
created schedule windows. They never write the device directly — per-window
durations live in the schedule model and are pushed by the reconciler.
"""
from homeassistant.components.number import NumberEntity

from .const import DOMAIN, EVENT_UPDATED
from .entity import AromaLinkEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Aroma-Link numbers based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    device_coordinators = data["device_coordinators"]
    store = data["store"]

    entities = []
    for device_id, coordinator in device_coordinators.items():
        device_name = coordinator.device_name
        entities.append(
            AromaLinkDefaultNumber(
                coordinator, entry, device_id, device_name, store,
                suffix="work_duration",
                name_suffix="Work Duration",
                field="default_work_sec",
                icon="mdi:spray",
                step=1,
            )
        )
        entities.append(
            AromaLinkDefaultNumber(
                coordinator, entry, device_id, device_name, store,
                suffix="pause_duration",
                name_suffix="Pause Duration",
                field="default_pause_sec",
                icon="mdi:timer-pause",
                step=5,
            )
        )

    async_add_entities(entities)


class AromaLinkDefaultNumber(AromaLinkEntity, NumberEntity):
    """Store-backed default duration."""

    _attr_native_min_value = 5
    _attr_native_max_value = 900
    _attr_native_unit_of_measurement = "seconds"
    _attr_mode = "box"

    def __init__(
        self, coordinator, entry, device_id, device_name, store,
        suffix, name_suffix, field, icon, step,
    ):
        super().__init__(coordinator, entry, device_id, device_name, suffix, name_suffix)
        self._store = store
        self._field = field
        self._attr_icon = icon
        self._attr_native_step = step

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        def _on_updated(event):
            if event.data.get("device_id") == str(self._device_id) and event.data.get(
                "change"
            ) == "flags":
                self.async_write_ha_state()

        self.async_on_remove(self.hass.bus.async_listen(EVENT_UPDATED, _on_updated))

    @property
    def native_value(self):
        return getattr(self._store.get_model(self._device_id), self._field)

    async def async_set_native_value(self, value):
        kwargs = {
            "work_sec" if self._field == "default_work_sec" else "pause_sec": int(value)
        }
        await self._store.async_set_defaults(self._device_id, **kwargs)
