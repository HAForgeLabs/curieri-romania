"""Senzori pentru Curieri Romania."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ADMIN_ENTRY_TITLE, CONF_ENTRY_TYPE, DATE_VERIFICARE_LICENTA, DOMAIN, ENTRY_TYPE_ADMIN, SIGNAL_LICENSE_UPDATED, SIGNAL_NOTIFICATION_SETTINGS_UPDATED, VERSION
from .coordinator import CurieriRomaniaCoordinator
from .models import Parcel, ParcelEvent, ParcelSnapshot
from .status import NormalizedStatus
from .license import async_obtine_licenta_globala, mascheaza_cheia_licenta
from .storage import CurieriRomaniaNotificationSettingsStore, CurieriRomaniaNotificationStore


@dataclass(frozen=True, kw_only=True)
class CurieriRomaniaSensorDescription(SensorEntityDescription):
    """Descriere pentru senzorii Curieri Romania."""

    value_fn: Callable[[ParcelSnapshot], int]
    extra_attrs_fn: Callable[[ParcelSnapshot], dict[str, Any]] | None = None


SENSOR_DESCRIPTIONS: tuple[CurieriRomaniaSensorDescription, ...] = (
    CurieriRomaniaSensorDescription(
        key="active_parcels",
        name="Colete active",
        translation_key="active_parcels",
        value_fn=lambda data: data.active_count,
        extra_attrs_fn=lambda data: _common_attributes(data),
    ),
    CurieriRomaniaSensorDescription(
        key="problem_parcels",
        name="Colete cu probleme",
        translation_key="problem_parcels",
        value_fn=lambda data: data.problem_count,
        extra_attrs_fn=lambda data: _common_attributes(data),
    ),
    CurieriRomaniaSensorDescription(
        key="delivered_parcels",
        name="Colete livrate",
        translation_key="delivered_parcels",
        value_fn=lambda data: data.delivered_count,
        extra_attrs_fn=lambda data: _common_attributes(data),
    ),
)


FINAL_STATUS_VALUES = {
    NormalizedStatus.DELIVERED,
    NormalizedStatus.RETURNED,
    NormalizedStatus.CANCELLED,
}


def _common_attributes(data: ParcelSnapshot) -> dict[str, Any]:
    """Returneaza atribute comune, fara date sensibile."""

    return {
        "updated_at": data.updated_at.isoformat() if data.updated_at else None,
        "errors": data.errors,
        "debug": data.debug,
        "parcels_total": len(data.parcels),
        "by_courier": _count_by(data, "courier"),
        "by_direction": _count_by(data, "direction"),
        "by_status": _count_by(data, "normalized_status"),
    }


def _count_by(data: ParcelSnapshot, field_name: str) -> dict[str, int]:
    """Numara coletele dupa un camp sigur."""

    counts: dict[str, int] = {}
    for parcel in data.parcels:
        value = getattr(parcel, field_name)
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _current_parcels(data: ParcelSnapshot | None) -> list[Parcel]:
    """Returneaza coletele disponibile in snapshotul curent."""

    if data is None:
        return []
    return list(data.parcels)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Configureaza senzorii pentru un config entry."""

    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_ADMIN:
        async_add_entities([
            CurieriRomaniaLicenseSensor(entry, "status", "Status licenta"),
            CurieriRomaniaLicenseSensor(entry, "plan", "Plan licenta"),
            CurieriRomaniaLicenseSensor(entry, "expires_at", "Valabila pana la"),
            CurieriRomaniaLicenseSensor(entry, "checked_at", "Ultima verificare licenta"),
            CurieriRomaniaLicenseSensor(entry, "utilizator", "Cont licenta"),
            CurieriRomaniaLicenseSensor(entry, "masked_key", "Cod licenta mascat"),
            CurieriRomaniaLicenseSensor(entry, "message", "Mesaj licenta"),
            CurieriRomaniaNotificationSettingSensor(entry, "notifications_enabled", "Notificari active"),
            CurieriRomaniaNotificationSettingSensor(entry, "notify_service", "Serviciu notificare"),
            CurieriRomaniaNotificationSettingSensor(entry, "notify_new_parcel", "Notificari colet nou"),
            CurieriRomaniaNotificationSettingSensor(entry, "notify_status_change", "Notificari schimbare status"),
            CurieriRomaniaNotificationSettingSensor(entry, "notify_out_for_delivery", "Notificari in livrare"),
            CurieriRomaniaNotificationSettingSensor(entry, "notify_pickup", "Notificari ridicare"),
            CurieriRomaniaNotificationSettingSensor(entry, "notify_delivered", "Notificari livrare finalizata"),
            CurieriRomaniaNotificationSettingSensor(entry, "notify_problems", "Notificari probleme"),
            CurieriRomaniaNotificationSettingSensor(entry, "notify_returned", "Notificari retur"),
            CurieriRomaniaNotificationSettingSensor(entry, "last_notification_at", "Ultima notificare"),
            CurieriRomaniaNotificationSettingSensor(entry, "last_notification_title", "Ultimul titlu notificare"),
            CurieriRomaniaNotificationSettingSensor(entry, "last_notification_target", "Ultima tinta notificare"),
            CurieriRomaniaNotificationSettingSensor(entry, "last_notification_result", "Ultimul rezultat notificare"),
            CurieriRomaniaNotificationSettingSensor(entry, "last_notification_error", "Ultima eroare notificare"),
            CurieriRomaniaNotificationHistorySensor(entry, "total_states", "Istoric notificari memorate"),
            CurieriRomaniaNotificationHistorySensor(entry, "real_states", "Istoric colete reale"),
            CurieriRomaniaNotificationHistorySensor(entry, "test_states", "Istoric teste notificari"),
            CurieriRomaniaNotificationHistorySensor(entry, "last_cleanup_at", "Ultima curatare istoric"),
            CurieriRomaniaNotificationHistorySensor(entry, "last_cleanup_removed", "Stari curatate ultima data"),
        ])
        return

    coordinator: CurieriRomaniaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        CurieriRomaniaSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    ]

    known_parcels: set[str] = set()
    for parcel in _current_parcels(coordinator.data):
        known_parcels.add(parcel.unique_key)
        entities.append(CurieriRomaniaParcelSensor(coordinator, entry, parcel.unique_key))

    async_add_entities(entities)

    @callback
    def _async_add_new_parcel_entities() -> None:
        """Adauga entitati pentru colete aparute dupa setup."""

        new_entities: list[CurieriRomaniaParcelSensor] = []
        for parcel in _current_parcels(coordinator.data):
            if parcel.unique_key in known_parcels:
                continue
            known_parcels.add(parcel.unique_key)
            new_entities.append(CurieriRomaniaParcelSensor(coordinator, entry, parcel.unique_key))

        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_parcel_entities))


def _admin_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Returneaza dispozitivul administrativ pentru licenta."""

    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=ADMIN_ENTRY_TITLE,
        manufacturer="HAForge Labs",
        model="Curieri Romania Admin",
        sw_version=VERSION,
    )


class CurieriRomaniaLicenseSensor(SensorEntity):
    """Senzor global pentru statusul licentei."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:shield-key-outline"

    _OBJECT_IDS = {
        "status": "status_licenta",
        "plan": "plan_licenta",
        "expires_at": "valabila_pana_la",
        "checked_at": "ultima_verificare_licenta",
        "utilizator": "cont_licenta",
        "masked_key": "cod_licenta_mascat",
        "message": "mesaj_licenta",
    }

    def __init__(self, entry: ConfigEntry, key: str, name: str) -> None:
        """Initializeaza senzorul de licenta."""

        self._entry = entry
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_license_v2_{key}"
        object_id = self._OBJECT_IDS.get(key, key)
        self._attr_suggested_object_id = f"{DOMAIN}_{object_id}"
        self._attr_device_info = _admin_device_info(entry)
        self._attr_native_value = "-"

    async def async_added_to_hass(self) -> None:
        """Asculta actualizarile licentei."""

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_LICENSE_UPDATED,
                self._handle_license_updated,
            )
        )
        await self._async_refresh_value()

    async def _handle_license_updated(self) -> None:
        """Actualizeaza valoarea dupa schimbarea licentei."""

        await self._async_refresh_value()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Actualizeaza manual valoarea senzorului."""

        await self._async_refresh_value()

    async def _async_refresh_value(self) -> None:
        """Citeste valoarea din storage-ul de licenta."""

        storage = await async_obtine_licenta_globala(self.hass)
        storage = storage if isinstance(storage, dict) else {}
        info = storage.get(DATE_VERIFICARE_LICENTA)
        info = info if isinstance(info, dict) else {}

        if self._key == "utilizator":
            self._attr_native_value = str(storage.get("utilizator", "") or "").strip() or "-"
            return

        if self._key == "masked_key":
            self._attr_native_value = mascheaza_cheia_licenta(str(storage.get("cheie_licenta", "") or "").strip()) or "-"
            return

        if self._key == "message":
            value = info.get("message")
            self._attr_native_value = str(value).strip() if value not in (None, "") else "-"
            return

        value = info.get(self._key)
        self._attr_native_value = str(value).strip() if value not in (None, "") else "-"


class CurieriRomaniaNotificationSettingSensor(SensorEntity):
    """Senzor de diagnostic pentru setarile notificarilor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:bell-cog-outline"

    _OBJECT_IDS = {
        "notifications_enabled": "notificari_active",
        "notify_service": "serviciu_notificare",
        "notify_new_parcel": "notificari_colet_nou",
        "notify_status_change": "notificari_schimbare_status",
        "notify_out_for_delivery": "notificari_in_livrare",
        "notify_pickup": "notificari_ridicare",
        "notify_delivered": "notificari_livrare_finalizata",
        "notify_problems": "notificari_probleme",
        "notify_returned": "notificari_retur",
        "last_notification_at": "ultima_notificare",
        "last_notification_title": "ultimul_titlu_notificare",
        "last_notification_target": "ultima_tinta_notificare",
        "last_notification_result": "ultimul_rezultat_notificare",
        "last_notification_error": "ultima_eroare_notificare",
    }

    def __init__(self, entry: ConfigEntry, key: str, name: str) -> None:
        """Initializeaza senzorul pentru setari notificari."""

        self._entry = entry
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_notification_settings_{key}"
        object_id = self._OBJECT_IDS.get(key, key)
        self._attr_suggested_object_id = f"{DOMAIN}_{object_id}"
        self._attr_device_info = _admin_device_info(entry)
        self._attr_native_value = "-"

    async def async_added_to_hass(self) -> None:
        """Asculta modificarile setarilor de notificari."""

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_NOTIFICATION_SETTINGS_UPDATED,
                self._handle_settings_updated,
            )
        )
        await self._async_refresh_value()

    async def _handle_settings_updated(self) -> None:
        """Actualizeaza valoarea dupa salvarea setarilor."""

        await self._async_refresh_value()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Actualizeaza manual valoarea senzorului."""

        await self._async_refresh_value()

    async def _async_refresh_value(self) -> None:
        """Citeste setarea din storage."""

        settings = await CurieriRomaniaNotificationSettingsStore(self.hass).async_get_settings()
        value = settings.get(self._key)
        if isinstance(value, bool):
            self._attr_native_value = "on" if value else "off"
        else:
            self._attr_native_value = str(value or "-").strip() or "-"


class CurieriRomaniaNotificationHistorySensor(SensorEntity):
    """Senzor de diagnostic pentru istoricul notificarilor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:database-clock-outline"

    _OBJECT_IDS = {
        "total_states": "istoric_notificari_memorate",
        "real_states": "istoric_colete_reale",
        "test_states": "istoric_teste_notificari",
        "last_cleanup_at": "ultima_curatare_istoric",
        "last_cleanup_removed": "stari_curatate_ultima_data",
    }

    def __init__(self, entry: ConfigEntry, key: str, name: str) -> None:
        """Initializeaza senzorul pentru istoricul notificarilor."""

        self._entry = entry
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_notification_history_{key}"
        object_id = self._OBJECT_IDS.get(key, key)
        self._attr_suggested_object_id = f"{DOMAIN}_{object_id}"
        self._attr_device_info = _admin_device_info(entry)
        self._attr_native_value = "-"

    async def async_added_to_hass(self) -> None:
        """Asculta modificarile notificarilor."""

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_NOTIFICATION_SETTINGS_UPDATED,
                self._handle_history_updated,
            )
        )
        await self._async_refresh_value()

    async def _handle_history_updated(self) -> None:
        """Actualizeaza valoarea dupa modificarea istoricului."""

        await self._async_refresh_value()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Actualizeaza manual valoarea senzorului."""

        await self._async_refresh_value()

    async def _async_refresh_value(self) -> None:
        """Citeste diagnosticul din storage."""

        diagnostics = await CurieriRomaniaNotificationStore(self.hass).async_get_diagnostics()
        value = diagnostics.get(self._key)
        self._attr_native_value = str(value or "-").strip() or "-"



class CurieriRomaniaSensor(CoordinatorEntity[CurieriRomaniaCoordinator], SensorEntity):
    """Senzor agregat pentru colete."""

    entity_description: CurieriRomaniaSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CurieriRomaniaCoordinator,
        entry: ConfigEntry,
        description: CurieriRomaniaSensorDescription,
    ) -> None:
        """Initializeaza senzorul."""

        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> int:
        """Returneaza valoarea senzorului."""

        if self.coordinator.data is None:
            return 0
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Returneaza atribute suplimentare."""

        if self.coordinator.data is None or self.entity_description.extra_attrs_fn is None:
            return None
        return self.entity_description.extra_attrs_fn(self.coordinator.data)


class CurieriRomaniaParcelSensor(CoordinatorEntity[CurieriRomaniaCoordinator], SensorEntity):
    """Senzor individual pentru un colet."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: CurieriRomaniaCoordinator,
        entry: ConfigEntry,
        parcel_key: str,
    ) -> None:
        """Initializeaza senzorul individual."""

        super().__init__(coordinator)
        self._entry = entry
        self._parcel_key = parcel_key
        self._attr_unique_id = f"{entry.entry_id}_parcel_{parcel_key}"
        self._attr_device_info = _device_info(entry)

    @property
    def name(self) -> str:
        """Returneaza numele entitatii."""

        parcel = self._parcel
        if parcel is None:
            return f"{self._entry.title} colet necunoscut"

        parts = [self._entry.title, _short_awb(parcel.awb)]
        if parcel.sender:
            parts.append(parcel.sender)
        elif parcel.locker_name:
            parts.append(parcel.locker_name)
        return " - ".join(parts)

    @property
    def native_value(self) -> str:
        """Returneaza statusul normalizat al coletului."""

        parcel = self._parcel
        if parcel is None:
            return NormalizedStatus.UNKNOWN.value
        return parcel.normalized_status.value

    @property
    def icon(self) -> str:
        """Returneaza o pictograma potrivita statusului."""

        parcel = self._parcel
        if parcel is None:
            return "mdi:package-variant"
        if parcel.normalized_status == NormalizedStatus.AVAILABLE_LOCKER:
            return "mdi:locker"
        if parcel.normalized_status == NormalizedStatus.AVAILABLE_PICKUP_POINT:
            return "mdi:store-marker"
        if parcel.normalized_status == NormalizedStatus.OUT_FOR_DELIVERY:
            return "mdi:truck-delivery"
        if parcel.normalized_status == NormalizedStatus.DELIVERED:
            return "mdi:package-check"
        if parcel.normalized_status in FINAL_STATUS_VALUES:
            return "mdi:package-variant-closed"
        if parcel.has_problem:
            return "mdi:package-variant-remove"
        return "mdi:package-variant"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Returneaza detaliile coletului."""

        parcel = self._parcel
        if parcel is None:
            return {"parcel_key": self._parcel_key, "missing_from_latest_update": True}

        return {
            "display_name": _parcel_display_name(parcel),
            "awb": parcel.awb,
            "awb_short": _short_awb(parcel.awb),
            "courier": parcel.courier,
            "direction": str(parcel.direction),
            "original_status": parcel.original_status,
            "normalized_status": str(parcel.normalized_status),
            "last_update": parcel.last_update.isoformat() if parcel.last_update else None,
            "sender": parcel.sender,
            "recipient": parcel.recipient,
            "current_location": parcel.current_location,
            "locker_name": parcel.locker_name,
            "locker_pin": parcel.locker_pin,
            "courier_phone": parcel.courier_phone,
            "delivery_time_window": parcel.delivery_time_window,
            "delivery_point_address": parcel.delivery_point_address,
            "delivery_type": parcel.raw.get("delivery_type") if isinstance(parcel.raw, dict) else None,
            "estimated_delivery": parcel.estimated_delivery.isoformat() if parcel.estimated_delivery else None,
            "delivered_at": parcel.delivered_at.isoformat() if parcel.delivered_at else None,
            "pin_expiration_at": parcel.pin_expiration_at.isoformat() if parcel.pin_expiration_at else None,
            "cash_on_delivery": parcel.cash_on_delivery,
            "is_locker": parcel.is_locker,
            "is_pickup_point": parcel.is_pickup_point,
            "is_return": parcel.is_return,
            "is_final": parcel.is_final,
            "has_problem": parcel.has_problem,
            "events": [_event_attributes(event) for event in parcel.events],
            "debug_source": parcel.raw.get("debug_source") if isinstance(parcel.raw, dict) else None,
        }

    @property
    def _parcel(self) -> Parcel | None:
        """Gaseste coletul in snapshotul curent."""

        if self.coordinator.data is None:
            return None
        for parcel in self.coordinator.data.parcels:
            if parcel.unique_key == self._parcel_key:
                return parcel
        return None


def _short_awb(awb: str) -> str:
    """Returneaza un AWB scurt pentru afisare."""

    if len(awb) <= 12:
        return awb
    return f"{awb[:6]}...{awb[-4:]}"


def _parcel_display_name(parcel: Parcel) -> str:
    """Construieste un nume util pentru un colet."""

    details = parcel.sender or parcel.locker_name or parcel.current_location
    if details:
        return f"{parcel.courier.title()} - {_short_awb(parcel.awb)} - {details}"
    return f"{parcel.courier.title()} - {_short_awb(parcel.awb)}"


def _event_attributes(event: ParcelEvent) -> dict[str, Any]:
    """Serializeaza un eveniment pentru atribute HA."""

    return {
        "status": event.status,
        "normalized_status": str(event.normalized_status),
        "status_id": event.status_id,
        "location": event.location,
        "event_time": event.event_time.isoformat() if event.event_time else None,
    }


def _device_info(entry: ConfigEntry) -> dict[str, Any]:
    """Returneaza informatii de dispozitiv pentru entry."""

    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": entry.title,
        "manufacturer": "HAForge Labs",
        "model": "Curieri Romania",
    }
