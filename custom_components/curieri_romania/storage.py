"""Persistenta locala pentru Curieri Romania."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_NOTIFICATIONS, STORAGE_VERSION_NOTIFICATIONS

DEFAULT_NOTIFICATION_HISTORY_RETENTION_DAYS = 180
DEFAULT_NOTIFICATION_HISTORY_MAX_STATES = 500
TEST_NOTIFICATION_RETENTION_DAYS = 7


def _utcnow() -> datetime:
    """Returneaza timpul curent in UTC."""

    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    """Parseaza defensiv o data ISO salvata in storage."""

    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _state_datetime(state: dict[str, Any]) -> datetime | None:
    """Returneaza cea mai relevanta data salvata pentru o stare de colet."""

    return (
        _parse_datetime(state.get("seen_at"))
        or _parse_datetime(state.get("last_update"))
        or _parse_datetime(state.get("saved_at"))
    )


class CurieriRomaniaNotificationStore:
    """Stocheaza local statusurile deja procesate pentru notificari."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initializeaza store-ul Home Assistant."""

        self._store: Store = Store(
            hass,
            STORAGE_VERSION_NOTIFICATIONS,
            STORAGE_KEY_NOTIFICATIONS,
        )
        self._data: dict[str, Any] = {
            "initialized": False,
            "parcels": {},
            "last_cleanup_at": "",
            "last_cleanup_removed": 0,
        }
        self._loaded = False

    async def async_load(self) -> None:
        """Incarca datele salvate local."""

        if self._loaded:
            return

        data = await self._store.async_load()
        if isinstance(data, dict):
            parcels = data.get("parcels")
            self._data = {
                "initialized": bool(data.get("initialized", False)),
                "parcels": parcels if isinstance(parcels, dict) else {},
                "last_cleanup_at": str(data.get("last_cleanup_at") or ""),
                "last_cleanup_removed": int(data.get("last_cleanup_removed") or 0),
            }

        self._loaded = True

    async def async_is_initialized(self) -> bool:
        """Returneaza daca prima scanare a fost deja memorata."""

        await self.async_load()
        return bool(self._data.get("initialized", False))

    async def async_get_parcel_state(self, key: str) -> dict[str, Any] | None:
        """Returneaza starea salvata pentru un colet."""

        await self.async_load()
        parcels = self._data.get("parcels")
        if not isinstance(parcels, dict):
            return None
        value = parcels.get(key)
        return deepcopy(value) if isinstance(value, dict) else None

    async def async_set_parcel_state(self, key: str, state: dict[str, Any]) -> None:
        """Salveaza starea curenta pentru un colet."""

        await self.async_load()
        parcels = self._data.setdefault("parcels", {})
        if not isinstance(parcels, dict):
            parcels = {}
            self._data["parcels"] = parcels
        clean_state = deepcopy(state)
        clean_state["saved_at"] = _utcnow().isoformat()
        parcels[key] = clean_state
        await self._store.async_save(self._data)

    async def async_delete_parcel_state(self, key: str) -> None:
        """Sterge starea salvata pentru un colet."""

        await self.async_load()
        parcels = self._data.get("parcels")
        if not isinstance(parcels, dict) or key not in parcels:
            return
        parcels.pop(key, None)
        await self._store.async_save(self._data)

    async def async_delete_states_with_prefix(self, prefix: str) -> int:
        """Sterge starile ale caror chei incep cu prefixul primit."""

        await self.async_load()
        parcels = self._data.get("parcels")
        if not isinstance(parcels, dict):
            return 0

        keys = [key for key in parcels if str(key).startswith(prefix)]
        for key in keys:
            parcels.pop(key, None)

        if keys:
            self._data["last_cleanup_at"] = _utcnow().isoformat()
            self._data["last_cleanup_removed"] = len(keys)
            await self._store.async_save(self._data)
        return len(keys)

    async def async_cleanup(
        self,
        *,
        current_keys: set[str] | None = None,
        retention_days: int = DEFAULT_NOTIFICATION_HISTORY_RETENTION_DAYS,
        max_states: int = DEFAULT_NOTIFICATION_HISTORY_MAX_STATES,
    ) -> dict[str, Any]:
        """Curata istoricul de notificari vechi, fara sa atinga coletele curente."""

        await self.async_load()
        parcels = self._data.get("parcels")
        if not isinstance(parcels, dict):
            self._data["parcels"] = {}
            return {"removed": 0, "total": 0, "reason": "reset_invalid_storage"}

        current_keys = current_keys or set()
        now = _utcnow()
        retention_limit = now - timedelta(days=max(1, int(retention_days)))
        test_limit = now - timedelta(days=TEST_NOTIFICATION_RETENTION_DAYS)
        keys_to_remove: set[str] = set()

        for key, state in list(parcels.items()):
            key_text = str(key)
            if key_text in current_keys:
                continue
            state = state if isinstance(state, dict) else {}
            state_time = _state_datetime(state)
            if key_text.startswith("__test__"):
                if state_time is not None and state_time < test_limit:
                    keys_to_remove.add(key_text)
                continue
            if bool(state.get("is_final")) and state_time is not None and state_time < retention_limit:
                keys_to_remove.add(key_text)

        if len(parcels) - len(keys_to_remove) > max_states:
            candidates = []
            for key, state in parcels.items():
                key_text = str(key)
                if key_text in current_keys or key_text in keys_to_remove:
                    continue
                state = state if isinstance(state, dict) else {}
                candidates.append((
                    _state_datetime(state) or datetime.min.replace(tzinfo=timezone.utc),
                    key_text,
                ))
            candidates.sort(key=lambda item: item[0])
            overflow = len(parcels) - len(keys_to_remove) - max_states
            for _state_time, key_text in candidates[:max(0, overflow)]:
                keys_to_remove.add(key_text)

        for key in keys_to_remove:
            parcels.pop(key, None)

        if keys_to_remove:
            self._data["last_cleanup_at"] = now.isoformat()
            self._data["last_cleanup_removed"] = len(keys_to_remove)
            await self._store.async_save(self._data)

        return {
            "removed": len(keys_to_remove),
            "total": len(parcels),
            "retention_days": retention_days,
            "max_states": max_states,
        }

    async def async_get_diagnostics(self) -> dict[str, Any]:
        """Returneaza diagnostic sigur despre istoricul de notificari."""

        await self.async_load()
        parcels = self._data.get("parcels")
        parcels = parcels if isinstance(parcels, dict) else {}
        test_count = sum(1 for key in parcels if str(key).startswith("__test__"))
        real_count = max(0, len(parcels) - test_count)
        dates = [
            _state_datetime(state)
            for state in parcels.values()
            if isinstance(state, dict) and _state_datetime(state) is not None
        ]
        return {
            "initialized": bool(self._data.get("initialized", False)),
            "total_states": len(parcels),
            "real_states": real_count,
            "test_states": test_count,
            "oldest_seen_at": min(dates).isoformat() if dates else "",
            "newest_seen_at": max(dates).isoformat() if dates else "",
            "last_cleanup_at": str(self._data.get("last_cleanup_at") or ""),
            "last_cleanup_removed": int(self._data.get("last_cleanup_removed") or 0),
        }

    async def async_mark_initialized(self) -> None:
        """Marcheaza store-ul ca initializat."""

        await self.async_load()
        if self._data.get("initialized"):
            return
        self._data["initialized"] = True
        await self._store.async_save(self._data)

    async def async_save_all(self) -> None:
        """Salveaza datele curente."""

        await self.async_load()
        await self._store.async_save(self._data)


DEFAULT_NOTIFICATION_SETTINGS: dict[str, Any] = {
    "notifications_enabled": True,
    "notify_service": "",
    "notify_new_parcel": True,
    "notify_status_change": True,
    "notify_out_for_delivery": True,
    "notify_pickup": True,
    "notify_delivered": True,
    "notify_problems": True,
    "notify_returned": True,
    "last_notification_at": "",
    "last_notification_title": "",
    "last_notification_target": "",
    "last_notification_result": "",
    "last_notification_error": "",
}


class CurieriRomaniaNotificationSettingsStore:
    """Stocheaza local setarile pentru notificari."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initializeaza store-ul Home Assistant pentru setari."""

        from .const import STORAGE_KEY_NOTIFICATION_SETTINGS, STORAGE_VERSION_NOTIFICATION_SETTINGS

        self._store: Store = Store(
            hass,
            STORAGE_VERSION_NOTIFICATION_SETTINGS,
            STORAGE_KEY_NOTIFICATION_SETTINGS,
        )
        self._data: dict[str, Any] = deepcopy(DEFAULT_NOTIFICATION_SETTINGS)
        self._loaded = False

    async def async_load(self) -> None:
        """Incarca setarile salvate local."""

        if self._loaded:
            return

        data = await self._store.async_load()
        settings = deepcopy(DEFAULT_NOTIFICATION_SETTINGS)
        if isinstance(data, dict):
            for key, default_value in DEFAULT_NOTIFICATION_SETTINGS.items():
                if key not in data:
                    continue
                value = data.get(key)
                if isinstance(default_value, bool):
                    settings[key] = bool(value)
                else:
                    settings[key] = str(value or "").strip()
        self._data = settings
        self._loaded = True

    async def async_get_settings(self) -> dict[str, Any]:
        """Returneaza setarile curente."""

        await self.async_load()
        return deepcopy(self._data)

    async def async_update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Actualizeaza setarile primite si le salveaza."""

        await self.async_load()
        for key, default_value in DEFAULT_NOTIFICATION_SETTINGS.items():
            if key not in updates or key.startswith("last_notification_"):
                continue
            value = updates.get(key)
            if isinstance(default_value, bool):
                self._data[key] = bool(value)
            else:
                self._data[key] = str(value or "").strip()
        await self._store.async_save(self._data)
        return deepcopy(self._data)

    async def async_update_diagnostics(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Actualizeaza informatiile de diagnostic pentru notificari."""

        await self.async_load()
        for key in (
            "last_notification_at",
            "last_notification_title",
            "last_notification_target",
            "last_notification_result",
            "last_notification_error",
        ):
            if key in updates:
                self._data[key] = str(updates.get(key) or "").strip()
        await self._store.async_save(self._data)
        return deepcopy(self._data)
