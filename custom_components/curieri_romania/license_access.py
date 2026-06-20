"""Reguli de acces in functie de licenta pentru Curieri Romania."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_COURIER, CONF_ENTRY_TYPE, DATE_VERIFICARE_LICENTA, DOMAIN, ENTRY_TYPE_COURIER
from .license import async_obtine_licenta_globala, licenta_este_acceptata


def _courier_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Returneaza intrarile de curier active, ordonate stabil."""

    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_COURIER
    ]
    return sorted(entries, key=lambda item: item.entry_id)


def first_enabled_courier_entry_id(hass: HomeAssistant) -> str | None:
    """Returneaza prima intrare de curier permisa fara licenta activa."""

    entries = _courier_entries(hass)
    return entries[0].entry_id if entries else None


def first_enabled_courier_code(hass: HomeAssistant) -> str | None:
    """Returneaza codul primului curier permis fara licenta activa."""

    entries = _courier_entries(hass)
    if not entries:
        return None
    return str(entries[0].data.get(CONF_COURIER, "") or "").strip() or None


async def async_license_allows_all_couriers(hass: HomeAssistant) -> bool:
    """Returneaza True daca licenta permite toti curierii configurati."""

    storage = await async_obtine_licenta_globala(hass)
    storage = storage if isinstance(storage, dict) else {}
    info = storage.get(DATE_VERIFICARE_LICENTA)
    info = info if isinstance(info, dict) else {}
    return licenta_este_acceptata(info)


async def async_courier_allowed_by_license(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Verifica daca intrarea curierului este permisa de licenta curenta."""

    if entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_COURIER:
        return True

    if await async_license_allows_all_couriers(hass):
        return True

    first_entry_id = first_enabled_courier_entry_id(hass)
    if not first_entry_id:
        return True

    return entry.entry_id == first_entry_id


def locked_courier_attributes(entry: Any) -> dict[str, Any]:
    """Returneaza atribute sigure pentru un curier blocat de licenta."""

    return {
        "courier": str((getattr(entry, "data", {}) or {}).get(CONF_COURIER, "") or ""),
        "license_blocked": True,
        "motiv": "Curier dezactivat fara licenta activa.",
    }
