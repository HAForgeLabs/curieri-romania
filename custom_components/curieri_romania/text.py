"""Entitati text pentru Curieri Romania."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ADMIN_ENTRY_TITLE,
    CONF_ENTRY_TYPE,
    CONF_LICENSE_KEY,
    DOMAIN,
    ENTRY_TYPE_ADMIN,
    VERSION,
)
from .license import async_obtine_licenta_globala


LICENSE_TEXT_UNIQUE_SUFFIX = "license_v2_key_text"


def _admin_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Returneaza dispozitivul administrativ pentru entitatile de licenta."""

    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=ADMIN_ENTRY_TITLE,
        manufacturer="HAForge Labs",
        model="Curieri Romania Admin",
        sw_version=VERSION,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Configureaza entitatile text pentru intrarea de administrare."""

    if entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_ADMIN:
        return

    async_add_entities([CurieriRomaniaLicenseKeyText(entry)])


class CurieriRomaniaLicenseKeyText(TextEntity):
    """Camp text pentru introducerea codului de licenta."""

    _attr_has_entity_name = True
    _attr_name = "Cod licenta noua"
    _attr_icon = "mdi:key-variant"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = "text"
    _attr_native_min = 0
    _attr_native_max = 128

    def __init__(self, entry: ConfigEntry) -> None:
        """Initializeaza campul text."""

        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{LICENSE_TEXT_UNIQUE_SUFFIX}"
        self._attr_suggested_object_id = f"{DOMAIN}_cod_licenta_noua"
        self._attr_device_info = _admin_device_info(entry)
        self._value: str | None = None

    async def async_added_to_hass(self) -> None:
        """Citeste valoarea salvata local cand entitatea este adaugata."""

        storage = await async_obtine_licenta_globala(self.hass)
        key = str((storage if isinstance(storage, dict) else {}).get(CONF_LICENSE_KEY, "") or "").strip()
        self._value = key or "TRIAL"

    @property
    def native_value(self) -> str | None:
        """Returneaza cheia curenta nemascata pentru editare locala."""

        return self._value

    async def async_set_value(self, value: str) -> None:
        """Actualizeaza valoarea introdusa in UI, fara validare automata."""

        self._value = str(value or "").strip()[: self._attr_native_max]
        self.async_write_ha_state()
