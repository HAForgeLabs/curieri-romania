"""Notificari pentru Curieri Romania."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import time
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_ENTRY_TYPE,
    ENTRY_TYPE_COURIER,
    CONF_NOTIFICATIONS_ENABLED,
    CONF_NOTIFY_DELIVERED,
    CONF_NOTIFY_NEW_PARCEL,
    CONF_NOTIFY_OUT_FOR_DELIVERY,
    CONF_NOTIFY_PICKUP,
    CONF_NOTIFY_PROBLEMS,
    CONF_NOTIFY_RETURNED,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_STATUS_CHANGE,
    DOMAIN,
    SIGNAL_NOTIFICATION_SETTINGS_UPDATED,
)
from .models import Parcel, ParcelSnapshot
from .notification_routes import PERSISTENT_TARGET, route_targets
from .status import NormalizedStatus
from .storage import CurieriRomaniaNotificationSettingsStore, CurieriRomaniaNotificationStore

_LOGGER = logging.getLogger(__name__)



def _service_parts(service_name: str) -> tuple[str, str] | None:
    """Imparte un serviciu de notificare de forma notify.mobile_app_x."""

    value = str(service_name or "").strip()
    if not value:
        return None
    if "." not in value:
        value = f"notify.{value}"
    domain, service = value.split(".", 1)
    domain = domain.strip()
    service = service.strip()
    if not domain or not service:
        return None
    return domain, service


async def _async_record_notification_diagnostic(
    hass: HomeAssistant,
    *,
    title: str,
    target: str,
    result: str,
    error: str = "",
) -> None:
    """Salveaza ultima incercare de notificare pentru diagnostic."""

    await CurieriRomaniaNotificationSettingsStore(hass).async_update_diagnostics(
        {
            "last_notification_at": datetime.now(timezone.utc).isoformat(),
            "last_notification_title": title,
            "last_notification_target": target,
            "last_notification_result": result,
            "last_notification_error": error,
        }
    )
    async_dispatcher_send(hass, SIGNAL_NOTIFICATION_SETTINGS_UPDATED)


async def _async_send_notification(
    hass: HomeAssistant,
    title: str,
    message: str,
    notification_id: str,
    *,
    force_persistent: bool = False,
    targets: list[str] | None = None,
) -> set[str]:
    """Trimite independent la fiecare destinatie; raporteaza succesul real."""

    if targets is None:
        settings = await CurieriRomaniaNotificationSettingsStore(hass).async_get_settings()
        targets = route_targets(settings)
    if force_persistent:
        targets = [PERSISTENT_TARGET]
    successful: set[str] = set()
    failed: list[str] = []
    for target in dict.fromkeys(targets):
        if target == PERSISTENT_TARGET:
            persistent_notification.async_create(hass, message, title=title, notification_id=notification_id)
            successful.add(target)
            continue
        parts = _service_parts(target)
        if parts is None or parts[0] != "notify":
            failed.append(target)
            continue
        try:
            async with asyncio.timeout(15):
                if hass.services.has_service(*parts):
                    await hass.services.async_call(*parts, {"title": title, "message": message}, blocking=True)
                elif hass.states.get(target) is not None:
                    await hass.services.async_call("notify", "send_message", {"entity_id": target, "title": title, "message": message}, blocking=True)
                else:
                    raise ValueError("Destinatia nu mai este disponibila.")
            successful.add(target)
        except Exception as err:  # Un telefon indisponibil nu blocheaza celelalte.
            failed.append(target)
            _LOGGER.warning("Curieri Romania: notificarea catre %s a esuat (%s).", target, type(err).__name__)
    if failed:
        # Nu include datele coletului in avertizarea comuna de eroare.
        persistent_notification.async_create(
            hass,
            "O notificare nu a ajuns la toate telefoanele selectate. Verifica destinatiile din Setari Curieri Romania.",
            title="Curieri Romania: telefon indisponibil",
            notification_id=f"{DOMAIN}_notification_delivery_error",
        )
    await _async_record_notification_diagnostic(
        hass, title=title, target=", ".join(targets) or "oprit",
        result="trimisa" if not failed and successful else "partial" if successful else "esuata" if failed else "oprita",
        error=("Destinatii indisponibile: " + ", ".join(failed)) if failed else "",
    )
    return successful


def _is_pickup_status(parcel: Parcel) -> bool:
    """Returneaza daca statusul indica ridicare din locker/punct."""

    return parcel.normalized_status in {
        NormalizedStatus.AVAILABLE_LOCKER,
        NormalizedStatus.AVAILABLE_PICKUP_POINT,
    }


def _should_send_for_settings(parcel: Parcel, *, is_new: bool, settings: dict[str, Any]) -> bool:
    """Decide daca notificarea este permisa de setarile utilizatorului."""

    if not bool(settings.get(CONF_NOTIFICATIONS_ENABLED, True)):
        return False
    if is_new:
        return bool(settings.get(CONF_NOTIFY_NEW_PARCEL, True))

    status = parcel.normalized_status
    if status == NormalizedStatus.OUT_FOR_DELIVERY:
        return bool(settings.get(CONF_NOTIFY_OUT_FOR_DELIVERY, True))
    if _is_pickup_status(parcel):
        return bool(settings.get(CONF_NOTIFY_PICKUP, True))
    if status == NormalizedStatus.DELIVERED:
        return bool(settings.get(CONF_NOTIFY_DELIVERED, True))
    if status in {NormalizedStatus.DELIVERY_FAILED, NormalizedStatus.PROBLEM}:
        return bool(settings.get(CONF_NOTIFY_PROBLEMS, True))
    if status == NormalizedStatus.RETURNED:
        return bool(settings.get(CONF_NOTIFY_RETURNED, True))
    return bool(settings.get(CONF_NOTIFY_STATUS_CHANGE, True))

_IMPORTANT_STATUSES = {
    NormalizedStatus.OUT_FOR_DELIVERY,
    NormalizedStatus.AVAILABLE_LOCKER,
    NormalizedStatus.AVAILABLE_PICKUP_POINT,
    NormalizedStatus.DELIVERED,
    NormalizedStatus.DELIVERY_FAILED,
    NormalizedStatus.RETURNED,
    NormalizedStatus.PROBLEM,
}


def _safe_key(value: str) -> str:
    """Normalizeaza o valoare pentru ID-uri de notificari."""

    return "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_")


def _notification_id(parcel: Parcel, suffix: str) -> str:
    """Construieste ID stabil pentru notificarea unui colet."""

    return f"{DOMAIN}_{_safe_key(parcel.courier)}_{_safe_key(parcel.awb)}_{suffix}"


def _status_value(parcel: Parcel) -> str:
    """Returneaza statusul normalizat ca text."""

    return str(parcel.normalized_status.value if hasattr(parcel.normalized_status, "value") else parcel.normalized_status)


def _status_fingerprint(parcel: Parcel) -> str:
    """Construieste amprenta folosita pentru evitarea notificarilor duplicate."""

    original = str(parcel.original_status or "").strip()
    normalized = _status_value(parcel)
    location = str(parcel.current_location or parcel.locker_name or parcel.delivery_point_address or "").strip()
    return f"{normalized}|{original}|{location}"


def _parcel_state(parcel: Parcel) -> dict[str, Any]:
    """Pregateste starea salvata pentru un colet."""

    return {
        "awb": parcel.awb,
        "courier": parcel.courier,
        "status": _status_value(parcel),
        "original_status": parcel.original_status or "",
        "fingerprint": _status_fingerprint(parcel),
        "last_update": parcel.last_update.isoformat() if parcel.last_update else None,
        "seen_at": datetime.now(timezone.utc).isoformat(),
        "is_final": bool(parcel.is_final),
    }


def _format_datetime(value: datetime | None) -> str | None:
    """Formateaza o data pentru notificare."""

    if value is None:
        return None
    try:
        local_value = value.astimezone()
    except Exception:
        local_value = value
    return local_value.strftime("%d.%m.%Y %H:%M")


def _parcel_label(parcel: Parcel) -> str:
    """Returneaza eticheta principala a coletului."""

    sender = str(parcel.sender or "").strip()
    recipient = str(parcel.recipient or "").strip()
    if sender:
        return sender
    if recipient:
        return recipient
    return parcel.awb


def _status_title(parcel: Parcel) -> str:
    """Returneaza titlul potrivit pentru statusul curent."""

    status = parcel.normalized_status
    courier = str(parcel.courier or "Curier")
    if status == NormalizedStatus.OUT_FOR_DELIVERY:
        return f"{courier}: colet in livrare"
    if status == NormalizedStatus.AVAILABLE_LOCKER:
        return f"{courier}: colet disponibil la locker"
    if status == NormalizedStatus.AVAILABLE_PICKUP_POINT:
        return f"{courier}: colet disponibil la ridicare"
    if status == NormalizedStatus.DELIVERED:
        return f"{courier}: colet livrat"
    if status in {NormalizedStatus.DELIVERY_FAILED, NormalizedStatus.PROBLEM}:
        return f"{courier}: problema la colet"
    if status == NormalizedStatus.RETURNED:
        return f"{courier}: colet returnat"
    return f"{courier}: status colet actualizat"


def _build_message(parcel: Parcel, *, is_new: bool) -> str:
    """Construieste mesajul notificarii."""

    lines: list[str] = []
    if is_new:
        lines.append("A fost detectat un colet nou.")
    else:
        lines.append("Statusul coletului s-a schimbat.")

    lines.extend(
        [
            "",
            f"Curier: {parcel.courier}",
            f"AWB: {parcel.awb}",
            f"Status: {_status_value(parcel)}",
        ]
    )

    if parcel.original_status:
        lines.append(f"Status original: {parcel.original_status}")

    label = _parcel_label(parcel)
    if label and label != parcel.awb:
        lines.append(f"Expeditor/Destinatar: {label}")

    location = parcel.current_location or parcel.locker_name or parcel.delivery_point_address
    if location:
        lines.append(f"Locatie: {location}")

    if parcel.cash_on_delivery is not None:
        lines.append(f"Ramburs: {parcel.cash_on_delivery:.2f} RON")

    last_update = _format_datetime(parcel.last_update)
    if last_update:
        lines.append(f"Ultima actualizare: {last_update}")

    lines.append("")
    lines.append("Deschide panelul Curieri Romania pentru detalii.")
    return "\n".join(lines)


def _should_notify_status_change(parcel: Parcel) -> bool:
    """Decide daca statusul curent merita notificare."""

    if parcel.normalized_status in _IMPORTANT_STATUSES:
        return True
    return bool(parcel.original_status)


async def async_handle_parcel_notifications(
    hass: HomeAssistant, snapshot: ParcelSnapshot, entry: Any = None,
) -> None:
    """Serializeaza actualizarile, inclusiv deduplicarea intre conturi."""
    lock = hass.data.setdefault(DOMAIN, {}).setdefault("notification_processing_lock", asyncio.Lock())
    async with lock:
        await _async_process_parcel_notifications(hass, snapshot, entry)


async def _async_process_parcel_notifications(hass: HomeAssistant, snapshot: ParcelSnapshot, entry: Any) -> None:
    store = CurieriRomaniaNotificationStore(hass)
    settings = await CurieriRomaniaNotificationSettingsStore(hass).async_get_settings()
    entry_id = str(getattr(entry, "entry_id", "") or "")
    account_name = str(getattr(entry, "title", "") or "")
    initialized = await store.async_is_initialized(entry_id)
    targets = route_targets(settings, entry_id)
    current_keys: set[str] = set()

    for parcel in snapshot.parcels:
        if not parcel.awb or not parcel.courier:
            continue
        key = f"account:{entry_id}:{parcel.unique_key}" if entry_id else parcel.unique_key
        current_keys.add(key)
        current_keys.add("__delivery__" + hashlib.sha256(parcel.unique_key.encode()).hexdigest())
        previous = await store.async_get_parcel_state(key)
        # Migrare aditiva: foloseste ultimul status vechi, fara avalansa de alerte.
        if previous is None and entry_id:
            previous = await store.async_get_parcel_state(parcel.unique_key)
        state = _parcel_state(parcel)
        fingerprint = state["fingerprint"]
        is_new = previous is None
        changed = is_new or previous.get("fingerprint") != fingerprint
        pending = previous.get("pending", {}) if previous and not changed else {}
        pending = {target: value for target, value in pending.items() if isinstance(value, dict)} if isinstance(pending, dict) else {}
        is_new_event = is_new if changed else bool(previous.get("event_is_new", False)) if previous else False
        state["event_is_new"] = is_new_event
        state["account_entry_id"] = entry_id
        state["pending"] = {}
        should_send = (changed or bool(pending)) and (initialized or previous is not None)
        should_send = should_send and _should_send_for_settings(parcel, is_new=is_new_event, settings=settings)
        should_send = should_send and (is_new_event or _should_notify_status_change(parcel))
        if should_send and targets:
            ledger_key = "__delivery__" + hashlib.sha256(parcel.unique_key.encode()).hexdigest()
            current_keys.add(ledger_key)
            ledger = await store.async_get_parcel_state(ledger_key) or {}
            sent = ledger.get("sent", {})
            sent = sent if isinstance(sent, dict) else {}
            event_hash = hashlib.sha256(f"{fingerprint}|{state.get('last_update') or ''}".encode()).hexdigest()
            now = time.time()
            selected = []
            for target in targets:
                history = sent.pop(target, [])
                history = history if isinstance(history, list) else []
                history = [item for item in history if isinstance(item, dict) and isinstance(item.get("at"), (int, float)) and now - item["at"] < 7 * 86400][-16:]
                sent[target] = history
                if any(item.get("event") == event_hash for item in history):
                    continue
                if not changed and (target not in pending or now < float(pending[target].get("retry_at", 0))):
                    if target in pending:
                        state["pending"][target] = pending[target]
                    continue
                selected.append(target)
            if selected:
                title = f"Curieri Romania: colet nou - {parcel.courier}" if is_new_event else _status_title(parcel)
                message = _build_message(parcel, is_new=is_new_event)
                if account_name:
                    message = f"Cont: {account_name}\n" + message
                success = await _async_send_notification(hass, title, message, _notification_id(parcel, "new" if is_new_event else "status"), targets=selected)
                for target in selected:
                    if target in success:
                        sent[target] = (sent.get(target, []) + [{"event": event_hash, "at": now}])[-16:]
                    else:
                        attempts = min(5, int(pending.get(target, {}).get("attempts", 0)) + 1)
                        state["pending"][target] = {"attempts": attempts, "retry_at": now + min(3600, 300 * 2 ** (attempts - 1))}
            # Limiteaza istoricul inclusiv cand au fost schimbate destinatiile.
            ledger = {**_parcel_state(parcel), "sent": {target: sent[target] for target in list(sent)[-24:]}}
            await store.async_set_parcel_state(ledger_key, ledger)
        await store.async_set_parcel_state(key, state)

    active_sets = hass.data.setdefault(DOMAIN, {}).setdefault("notification_current_keys", {})
    active_sets[entry_id] = current_keys
    all_keys = set().union(*active_sets.values()) if active_sets else set()
    cleanup = await store.async_cleanup(current_keys=all_keys)
    if cleanup.get("removed"):
        async_dispatcher_send(hass, SIGNAL_NOTIFICATION_SETTINGS_UPDATED)
    if not initialized:
        await store.async_mark_initialized(entry_id)


_TEST_PREFIX = "__test__"
_TEST_STATUS_KEY = f"{_TEST_PREFIX}_status_demo"
_TEST_STATUSES = [
    NormalizedStatus.REGISTERED,
    NormalizedStatus.IN_TRANSIT,
    NormalizedStatus.OUT_FOR_DELIVERY,
    NormalizedStatus.AVAILABLE_PICKUP_POINT,
    NormalizedStatus.DELIVERED,
]


def _test_notification_id(suffix: str) -> str:
    """Construieste ID pentru notificarile de test."""

    return f"{DOMAIN}_test_{suffix}"


async def async_send_test_notification(hass: HomeAssistant, entry_id: str = "") -> None:
    """Trimite o notificare simpla de test."""

    entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
    if entry_id and (entry is None or entry.domain != DOMAIN or entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_COURIER):
        raise ValueError("Contul selectat nu mai exista.")
    settings = await CurieriRomaniaNotificationSettingsStore(hass).async_get_settings()
    targets = route_targets(settings, entry_id)
    await _async_send_notification(
        hass,
        f"[TEST] Curieri Romania: {entry.title if entry else 'notificare test'}",
        (
            "[TEST] Sistemul de notificari Curieri Romania functioneaza.\n\n"
            "Aceasta notificare nu foloseste date reale si nu modifica lista de colete."
        ),
        _test_notification_id("simple"),
        targets=targets,
    )


async def async_simulate_new_parcel_notification(hass: HomeAssistant) -> None:
    """Simuleaza detectarea unui colet nou fara sa afecteze coletele reale."""

    now = datetime.now(timezone.utc)
    awb = f"TEST-{now.strftime('%Y%m%d-%H%M%S')}"
    key = f"{_TEST_PREFIX}_new_{awb.lower()}"
    store = CurieriRomaniaNotificationStore(hass)
    await store.async_set_parcel_state(
        key,
        {
            "awb": awb,
            "courier": "TEST Courier",
            "status": NormalizedStatus.REGISTERED.value,
            "original_status": "Test parcel created",
            "fingerprint": f"{NormalizedStatus.REGISTERED.value}|Test parcel created|Centru test",
            "last_update": now.isoformat(),
            "seen_at": now.isoformat(),
            "test": True,
        },
    )

    await _async_send_notification(
        hass,
        "[TEST] Curieri Romania: colet nou simulat",
        (
            "[TEST] A fost simulat un colet nou.\n\n"
            "Curier: TEST Courier\n"
            f"AWB: {awb}\n"
            "Status: inregistrat\n"
            "Locatie: Centru test\n\n"
            "Aceasta notificare nu provine de la un curier real si nu apare in lista de colete."
        ),
        _test_notification_id(f"new_{awb.lower()}"),
    )


async def async_simulate_status_change_notification(hass: HomeAssistant) -> None:
    """Simuleaza o schimbare de status pentru un colet fictiv."""

    now = datetime.now(timezone.utc)
    store = CurieriRomaniaNotificationStore(hass)
    previous = await store.async_get_parcel_state(_TEST_STATUS_KEY) or {}
    previous_index = int(previous.get("test_status_index", -1)) if str(previous.get("test_status_index", "")).lstrip("-").isdigit() else -1
    next_index = (previous_index + 1) % len(_TEST_STATUSES)
    status = _TEST_STATUSES[next_index]
    previous_status = str(previous.get("status") or "necunoscut")
    awb = "TEST-STATUS-0001"

    await store.async_set_parcel_state(
        _TEST_STATUS_KEY,
        {
            "awb": awb,
            "courier": "TEST Courier",
            "status": status.value,
            "original_status": f"Test status {next_index + 1}",
            "fingerprint": f"{status.value}|Test status {next_index + 1}|Traseu test",
            "last_update": now.isoformat(),
            "seen_at": now.isoformat(),
            "test": True,
            "test_status_index": next_index,
        },
    )

    await _async_send_notification(
        hass,
        "[TEST] Curieri Romania: status simulat",
        (
            "[TEST] A fost simulata o schimbare de status.\n\n"
            "Curier: TEST Courier\n"
            f"AWB: {awb}\n"
            f"Status anterior: {previous_status}\n"
            f"Status nou: {status.value}\n"
            "Locatie: Traseu test\n\n"
            "Aceasta notificare nu modifica niciun colet real."
        ),
        _test_notification_id("status_demo"),
    )


async def async_reset_test_notification_history(hass: HomeAssistant) -> int:
    """Sterge doar istoricul notificarilor de test."""

    store = CurieriRomaniaNotificationStore(hass)
    deleted = await store.async_delete_states_with_prefix(_TEST_PREFIX)
    persistent_notification.async_dismiss(hass, _test_notification_id("simple"))
    persistent_notification.async_dismiss(hass, _test_notification_id("status_demo"))
    title = "[TEST] Curieri Romania: istoric test resetat"
    persistent_notification.async_create(
        hass,
        (
            "[TEST] Istoricul notificarilor de test a fost resetat.\n\n"
            f"Inregistrari de test sterse: {deleted}.\n"
            "Istoricul coletelor reale nu a fost modificat."
        ),
        title=title,
        notification_id=_test_notification_id("reset"),
    )
    await _async_record_notification_diagnostic(
        hass,
        title=title,
        target="persistent_notification",
        result="trimisa",
    )
    return deleted
