"""Butoane pentru administrarea Curieri Romania."""

from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ADMIN_ENTRY_TITLE,
    CONF_ENTRY_TYPE,
    DOMAIN,
    ENTRY_TYPE_ADMIN,
    SIGNAL_LICENSE_UPDATED,
    VERSION,
)
from .license import (
    async_obtine_context_licenta,
    async_salveaza_licenta_globala,
    async_valideaza_licenta,
)
from .text import LICENSE_TEXT_UNIQUE_SUFFIX


def _license_text_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Gaseste entity_id pentru campul text al licentei."""

    registry = er.async_get(hass)
    unique_id = f"{entry.entry_id}_{LICENSE_TEXT_UNIQUE_SUFFIX}"
    return registry.async_get_entity_id("text", DOMAIN, unique_id)


def _admin_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Returneaza dispozitivul administrativ comun."""

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
    """Configureaza butoanele pentru intrarea de administrare."""

    if entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_ADMIN:
        return

    async_add_entities(
        [
            CurieriRomaniaAddServiceButton(hass, entry),
            CurieriRomaniaApplyLicenseButton(entry),
            CurieriRomaniaRefreshLicenseStatusButton(entry),
        ]
    )


class CurieriRomaniaAddServiceButton(ButtonEntity):
    """Buton administrativ pentru adaugarea unui serviciu de curierat."""

    _attr_has_entity_name = True
    _attr_translation_key = "add_service"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initializeaza butonul."""

        self.hass = hass
        self._attr_unique_id = f"{entry.entry_id}_add_service"
        self._attr_device_info = _admin_device_info(entry)

    async def async_press(self) -> None:
        """Afiseaza instructiuni pentru deschiderea flow-ului de adaugare serviciu."""

        message = (
            "Pentru a adauga un curier nou, mergi la Setari -> Dispozitive si servicii "
            "-> Adauga hub -> Curieri Romania. Se va deschide lista de servicii, "
            "unde poti alege Sameday, FAN Courier, Cargus sau GLS."
        )
        persistent_notification.async_create(
            self.hass,
            message,
            title="Curieri Romania - Adauga serviciu",
            notification_id="curieri_romania_add_service",
        )


class CurieriRomaniaApplyLicenseButton(ButtonEntity):
    """Buton pentru aplicarea codului de licenta introdus in entitatea text."""

    _attr_has_entity_name = True
    _attr_name = "Aplica licenta"
    _attr_icon = "mdi:key-chain-variant"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry) -> None:
        """Initializeaza butonul."""

        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_license_v2_apply"
        self._attr_suggested_object_id = f"{DOMAIN}_aplica_licenta"
        self._attr_device_info = _admin_device_info(entry)

    async def async_press(self) -> None:
        """Valideaza si aplica licenta introdusa."""

        text_entity_id = _license_text_entity_id(self.hass, self._entry)
        if not text_entity_id:
            raise HomeAssistantError("Nu am gasit campul text pentru introducerea licentei.")

        state = self.hass.states.get(text_entity_id)
        license_key = str(state.state).strip() if state else ""
        if not license_key:
            raise HomeAssistantError("Introdu mai intai un cod de licenta sau TRIAL.")

        username, _current_key, _storage = await async_obtine_context_licenta(self.hass, intrare=self._entry)
        result = await async_valideaza_licenta(self.hass, license_key, username)
        await async_salveaza_licenta_globala(self.hass, license_key, username, result)

        await self.hass.services.async_call(
            "text",
            "set_value",
            {"entity_id": text_entity_id, "value": license_key},
            blocking=True,
        )

        dispatcher_send(self.hass, SIGNAL_LICENSE_UPDATED)

        notification_id = "curieri_romania_aplica_licenta"
        if not result.valid:
            message = result.message or "Codul de licenta nu a putut fi validat."
            persistent_notification.async_create(
                self.hass,
                f"Aplicarea licentei a esuat.\n\nMotiv: **{message}**",
                title="Curieri Romania - Licenta",
                notification_id=notification_id,
            )
            raise HomeAssistantError(message)

        persistent_notification.async_create(
            self.hass,
            (
                "Licenta a fost actualizata cu succes.\n\n"
                f"- Utilizator: **{result.username or username or '-'}**\n"
                f"- Plan: **{result.plan or '-'}**\n"
                f"- Expira la: **{result.expires_at or '-'}**"
            ),
            title="Curieri Romania - Licenta",
            notification_id=notification_id,
        )


class CurieriRomaniaRefreshLicenseStatusButton(ButtonEntity):
    """Buton pentru actualizarea statusului licentei."""

    _attr_has_entity_name = True
    _attr_name = "Actualizeaza status licenta"
    _attr_icon = "mdi:shield-sync-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry) -> None:
        """Initializeaza butonul."""

        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_license_v2_refresh"
        self._attr_suggested_object_id = f"{DOMAIN}_actualizeaza_status_licenta"
        self._attr_device_info = _admin_device_info(entry)

    async def async_press(self) -> None:
        """Revalideaza licenta salvata local."""

        username, license_key, _storage = await async_obtine_context_licenta(self.hass, intrare=self._entry)
        license_key = str(license_key or "").strip() or "TRIAL"

        result = await async_valideaza_licenta(self.hass, license_key, username)
        await async_salveaza_licenta_globala(self.hass, license_key, username, result)
        dispatcher_send(self.hass, SIGNAL_LICENSE_UPDATED)

        notification_id = "curieri_romania_actualizeaza_licenta"
        if not result.valid:
            message = result.message or "Licenta nu este valida."
            persistent_notification.async_create(
                self.hass,
                f"Statusul licentei a fost verificat.\n\nMotiv: **{message}**",
                title="Curieri Romania - Licenta",
                notification_id=notification_id,
            )
            raise HomeAssistantError(message)

        persistent_notification.async_create(
            self.hass,
            (
                "Statusul licentei a fost actualizat cu succes.\n\n"
                f"- Utilizator: **{result.username or username or '-'}**\n"
                f"- Plan: **{result.plan or '-'}**\n"
                f"- Expira la: **{result.expires_at or '-'}**"
            ),
            title="Curieri Romania - Licenta",
            notification_id=notification_id,
        )
