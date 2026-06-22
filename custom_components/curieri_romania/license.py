"""Validare si stocare licenta pentru Curieri Romania."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

try:
    from homeassistant.helpers.instance_id import async_get as async_get_instance_id
except ImportError:  # compatibilitate cu versiuni HA mai vechi
    async_get_instance_id = None

from .const import (
    CONF_LICENSE_KEY,
    CONF_LICENSE_USER,
    DATE_VERIFICARE_LICENTA,
    DEFAULT_LICENSE_GRACE_DAYS,
    DOMAIN,
    LICENSE_STATUS_ACTIVATION_LIMIT,
    LICENSE_STATUS_ACTIVE,
    LICENSE_STATUS_EXPIRED,
    LICENSE_STATUS_INVALID,
    LICENSE_STATUS_INVALID_PRODUCT,
    LICENSE_STATUS_REVOKED,
    LICENSE_STATUS_TRIAL,
    LICENSE_STATUS_UNKNOWN,
    STORAGE_KEY_INSTALLATION,
    STORAGE_KEY_LICENSE,
    STORAGE_VERSION_INSTALLATION,
    STORAGE_VERSION_LICENSE,
    URL_API_LICENTA,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

ACCEPTED_LICENSE_STATUSES = {LICENSE_STATUS_ACTIVE, LICENSE_STATUS_TRIAL}


@dataclass(slots=True)
class LicenseResult:
    """Rezultat standard pentru validarea licentei."""

    valid: bool
    status: str
    plan: str | None = None
    expires_at: str | None = None
    message: str | None = None
    checked_at: str | None = None
    connection_error: bool = False
    username: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Returneaza rezultatul ca dictionar serializabil."""

        return {
            "valid": self.valid,
            "status": self.status,
            "plan": self.plan,
            "expires_at": self.expires_at,
            "message": self.message,
            "checked_at": self.checked_at,
            "connection_error": self.connection_error,
            "username": self.username,
        }


RezultatLicenta = LicenseResult


def build_instance_fingerprint(hass: HomeAssistant) -> str:
    """Returneaza hash-ul anonim cache-uit pentru compatibilitate legacy.

    Functia veche era sincrona si construia fingerprint-ul din detalii ale
    instantei Home Assistant. Din motive de privacy, nu mai folosim acele date.
    Validarea licentei foloseste acum async_build_installation_hash().
    """

    cached_hash = hass.data.get(f"{DOMAIN}_installation_id_hash")
    if isinstance(cached_hash, str) and cached_hash:
        return cached_hash
    return hashlib.sha256(f"{DOMAIN}|legacy-no-instance-data".encode("utf-8")).hexdigest()


def construieste_fingerprint_instanta(hass: HomeAssistant) -> str:
    """Alias in romana pentru compatibilitate cu celelalte proiecte."""

    return build_instance_fingerprint(hass)


async def _async_get_local_installation_id(hass: HomeAssistant) -> str:
    """Returneaza un ID local random, folosit doar ca fallback anonim."""

    store = Store[dict[str, Any]](hass, STORAGE_VERSION_INSTALLATION, STORAGE_KEY_INSTALLATION)
    data = await store.async_load()
    data = data if isinstance(data, dict) else {}

    installation_id = str(data.get("installation_id", "") or "").strip()
    if not installation_id:
        installation_id = uuid.uuid4().hex
        await store.async_save({
            "installation_id": installation_id,
            "created_at": _now_utc_iso(),
        })

    return installation_id


async def async_build_installation_hash(hass: HomeAssistant) -> str:
    """Construieste un identificator anonim si stabil pentru licentiere.

    Nu foloseste numele locatiei, URL-uri, calea de configurare, IP-uri sau date
    despre colete. Preferam ID-ul intern Home Assistant, iar daca nu este
    disponibil folosim un UUID local generat random si stocat local.
    """

    cache_key = f"{DOMAIN}_installation_id_hash"
    cached_hash = hass.data.get(cache_key)
    if isinstance(cached_hash, str) and cached_hash:
        return cached_hash

    source_type = "local_uuid"
    source_value = ""

    if async_get_instance_id is not None:
        try:
            ha_instance_id = await async_get_instance_id(hass)
            source_value = str(ha_instance_id or "").strip()
            if source_value:
                source_type = "ha_instance_id"
        except Exception as err:  # noqa: BLE001 - fallback sigur, fara blocarea integrarii
            _LOGGER.debug("Curieri Romania: nu s-a putut citi instance_id HA: %s", err)

    if not source_value:
        source_value = await _async_get_local_installation_id(hass)

    installation_hash = hashlib.sha256(
        f"{DOMAIN}|{source_type}|{source_value}".encode("utf-8")
    ).hexdigest()
    hass.data[cache_key] = installation_hash
    return installation_hash


async def async_get_global_license(hass: HomeAssistant) -> dict[str, Any]:
    """Citeste licenta globala din storage."""

    store = Store[dict[str, Any]](hass, STORAGE_VERSION_LICENSE, STORAGE_KEY_LICENSE)
    data = await store.async_load()
    return data if isinstance(data, dict) else {}


async def async_obtine_licenta_globala(hass: HomeAssistant) -> dict[str, Any]:
    """Alias in romana pentru citirea licentei globale."""

    return await async_get_global_license(hass)


async def async_save_global_license(
    hass: HomeAssistant,
    license_key: str,
    username: str,
    result: LicenseResult | None = None,
) -> None:
    """Salveaza licenta globala si ultimul rezultat de verificare."""

    store = Store[dict[str, Any]](hass, STORAGE_VERSION_LICENSE, STORAGE_KEY_LICENSE)
    # Pastram local doar utilizatorul intors explicit de server. Nu mai salvam
    # fallback-uri locale precum numele instantei Home Assistant.
    final_username = str((result.username if result and result.username else "") or "").strip()
    payload: dict[str, Any] = {
        CONF_LICENSE_KEY: str(license_key).strip() or "TRIAL",
        CONF_LICENSE_USER: final_username,
    }

    if result is not None:
        payload[DATE_VERIFICARE_LICENTA] = result.as_dict()

    await store.async_save(payload)


async def async_salveaza_licenta_globala(
    hass: HomeAssistant,
    cheie_licenta: str,
    utilizator: str,
    rezultat: LicenseResult | None = None,
) -> None:
    """Alias in romana pentru salvarea licentei globale."""

    await async_save_global_license(hass, cheie_licenta, utilizator, rezultat)


def _default_license_username(hass: HomeAssistant) -> str:
    """Returneaza utilizatorul implicit pentru validare, fara date locale HA."""

    return ""


async def async_get_license_context(
    hass: HomeAssistant,
    entry: ConfigEntry | None = None,
    username: str | None = None,
    license_key: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Returneaza utilizatorul, cheia si storage-ul pentru validarea licentei."""

    storage = await async_get_global_license(hass)
    storage_username = str(storage.get(CONF_LICENSE_USER, "")).strip()
    storage_key = str(storage.get(CONF_LICENSE_KEY, "")).strip()

    entry_username = ""
    entry_key = ""
    if entry is not None:
        entry_username = str(entry.options.get(CONF_LICENSE_USER, entry.data.get(CONF_LICENSE_USER, ""))).strip()
        entry_key = str(entry.options.get(CONF_LICENSE_KEY, entry.data.get(CONF_LICENSE_KEY, ""))).strip()

    final_username = (
        str(username).strip()
        if username is not None
        else (storage_username or entry_username or _default_license_username(hass))
    )
    final_key = (
        str(license_key).strip()
        if license_key is not None
        else (storage_key or entry_key or "TRIAL")
    )
    return final_username, final_key, storage


async def async_obtine_context_licenta(
    hass: HomeAssistant,
    intrare: ConfigEntry | None = None,
    utilizator: str | None = None,
    cheie_licenta: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Alias in romana pentru contextul de licenta."""

    return await async_get_license_context(hass, intrare, utilizator, cheie_licenta)


def _stored_license_matches_context(storage: dict[str, Any], license_key: str, username: str) -> bool:
    """Verifica daca licenta din storage corespunde contextului curent."""

    key_ok = str(storage.get(CONF_LICENSE_KEY, "")).strip() == str(license_key).strip()
    stored_user = str(storage.get(CONF_LICENSE_USER, "")).strip()
    if not username or not stored_user:
        return key_ok
    return key_ok and stored_user == str(username).strip()


def _date_licenta_din_storage_sunt_pentru_contextul_curent(
    date_licenta_globala: dict[str, Any],
    cheie_licenta: str,
    utilizator: str,
) -> bool:
    """Alias intern pentru verificarea contextului de licenta."""

    return _stored_license_matches_context(date_licenta_globala, cheie_licenta, utilizator)


async def async_validate_license(
    hass: HomeAssistant,
    license_key: str,
    username: str,
) -> LicenseResult:
    """Valideaza licenta la serverul de licentiere."""

    session = async_get_clientsession(hass)
    installation_hash = await async_build_installation_hash(hass)
    payload = {
        "license_key": str(license_key or "").strip() or "TRIAL",
        "product": DOMAIN,
        "version": VERSION,
        "installation_id_hash": installation_hash,
        # Pastram temporar campul fingerprint pentru compatibilitate cu workerul
        # existent, dar valoarea este acelasi hash anonim, fara date locale HA.
        "fingerprint": installation_hash,
        # Camp pastrat gol pentru compatibilitate cu API-ul vechi.
        "username": "",
    }

    try:
        async with session.post(URL_API_LICENTA, json=payload, timeout=20) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:  # noqa: BLE001 - raspunsul poate fi text simplu
                data = {"message": await response.text()}

            if not isinstance(data, dict):
                data = {"message": "Raspuns invalid de la serverul de licentiere."}

            raw_status = str(data.get("status", LICENSE_STATUS_UNKNOWN)).strip().lower()
            valid = bool(data.get("valid", False) or data.get("active", False))

            terminal_invalid_statuses = {
                LICENSE_STATUS_REVOKED,
                LICENSE_STATUS_EXPIRED,
                LICENSE_STATUS_INVALID,
                LICENSE_STATUS_INVALID_PRODUCT,
                LICENSE_STATUS_ACTIVATION_LIMIT,
            }
            if raw_status in terminal_invalid_statuses:
                valid = False

            response_product = str(data.get("product") or data.get("domain") or "").strip()
            if valid and response_product and response_product != DOMAIN:
                valid = False
                raw_status = LICENSE_STATUS_INVALID_PRODUCT
                data["message"] = "Licenta este valida, dar apartine altei integrari."

            if response.status >= 400 and not valid:
                if response.status in (400, 401, 403, 404):
                    status = raw_status if raw_status in LICENSE_STATUS_LABELS else LICENSE_STATUS_INVALID
                else:
                    status = raw_status if raw_status in terminal_invalid_statuses else LICENSE_STATUS_UNKNOWN
            elif valid:
                status = LICENSE_STATUS_TRIAL if raw_status == LICENSE_STATUS_TRIAL else LICENSE_STATUS_ACTIVE
            else:
                status = raw_status if raw_status in LICENSE_STATUS_LABELS else LICENSE_STATUS_INVALID

            return LicenseResult(
                valid=valid,
                status=status,
                plan=data.get("plan") or data.get("license_plan"),
                expires_at=data.get("expires_at") or data.get("valid_until") or data.get("expires"),
                message=data.get("message") or data.get("error"),
                checked_at=_now_utc_iso(),
                connection_error=False,
                username=str(data.get("username", "")).strip() or None,
            )

    except (ClientError, TimeoutError, ValueError) as err:
        _LOGGER.warning("Curieri Romania: validarea licentei a esuat: %s", err)
        return LicenseResult(
            valid=False,
            status=LICENSE_STATUS_UNKNOWN,
            message=str(err),
            checked_at=_now_utc_iso(),
            connection_error=True,
        )


async def async_valideaza_licenta(
    hass: HomeAssistant,
    cheie_licenta: str,
    utilizator: str,
) -> LicenseResult:
    """Alias in romana pentru validarea licentei."""

    return await async_validate_license(hass, cheie_licenta, utilizator)


def extract_stored_license_result(entry: ConfigEntry | None = None, storage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extrage ultimul rezultat de licenta salvat local."""

    if storage is not None:
        value = storage.get(DATE_VERIFICARE_LICENTA)
        return value if isinstance(value, dict) else {}

    if entry is None:
        return {}

    value = entry.options.get(DATE_VERIFICARE_LICENTA) or entry.data.get(DATE_VERIFICARE_LICENTA) or {}
    return value if isinstance(value, dict) else {}


def extrage_date_licenta_stocate(intrare: ConfigEntry) -> dict[str, Any]:
    """Alias in romana pentru extragerea rezultatului de licenta."""

    return extract_stored_license_result(intrare)


def license_is_accepted(license_data: dict[str, Any]) -> bool:
    """Returneaza True daca licenta este activa sau trial valid."""

    return bool(license_data.get("valid")) and license_data.get("status") in ACCEPTED_LICENSE_STATUSES


def licenta_este_acceptata(date_licenta: dict[str, Any]) -> bool:
    """Alias in romana pentru verificarea acceptarii licentei."""

    return license_is_accepted(date_licenta)


def can_use_cached_license(
    license_data: dict[str, Any],
    grace_days: int = DEFAULT_LICENSE_GRACE_DAYS,
) -> bool:
    """Permite folosirea licentei din cache in perioada de gratie."""

    if not license_is_accepted(license_data):
        return False

    checked_at = license_data.get("checked_at")
    if not checked_at:
        return False

    try:
        checked_dt = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
    except ValueError:
        return False

    return datetime.now(UTC) <= checked_dt + timedelta(days=grace_days)


def se_poate_folosi_licenta_din_cache(
    date_licenta: dict[str, Any],
    zile_gratie: int = DEFAULT_LICENSE_GRACE_DAYS,
) -> bool:
    """Alias in romana pentru folosirea cache-ului de licenta."""

    return can_use_cached_license(date_licenta, zile_gratie)


def mask_license_key(key: str | None) -> str:
    """Mascheaza cheia de licenta pentru afisare si loguri."""

    if not key:
        return ""
    key = str(key).strip()
    if len(key) <= 4:
        return "*" * len(key)
    return f"{key[:4]}***{key[-2:]}"


def mascheaza_cheia_licenta(cheie: str | None) -> str:
    """Alias in romana pentru mascarea cheii de licenta."""

    return mask_license_key(cheie)


async def async_check_license(
    hass: HomeAssistant,
    entry: ConfigEntry | None = None,
) -> LicenseResult:
    """Verifica licenta, cu fallback sigur la cache local."""

    username, key, storage = await async_get_license_context(hass, entry=entry)
    result = await async_validate_license(hass, key, username)

    if result.valid:
        return result

    if result.connection_error:
        entry_cache = extract_stored_license_result(entry) if entry is not None else {}
        if can_use_cached_license(entry_cache):
            return LicenseResult(
                valid=True,
                status=entry_cache.get("status", LICENSE_STATUS_UNKNOWN),
                plan=entry_cache.get("plan"),
                expires_at=entry_cache.get("expires_at"),
                message=entry_cache.get("message"),
                checked_at=entry_cache.get("checked_at"),
                username=entry_cache.get("username"),
            )

        global_cache = extract_stored_license_result(storage=storage)
        if _stored_license_matches_context(storage, key, username) and can_use_cached_license(global_cache):
            return LicenseResult(
                valid=True,
                status=global_cache.get("status", LICENSE_STATUS_UNKNOWN),
                plan=global_cache.get("plan"),
                expires_at=global_cache.get("expires_at"),
                message=global_cache.get("message"),
                checked_at=global_cache.get("checked_at"),
                username=global_cache.get("username"),
            )

    return result


async def async_verifica_licenta(
    hass: HomeAssistant,
    intrare: ConfigEntry | None = None,
) -> LicenseResult:
    """Alias in romana pentru verificarea licentei."""

    return await async_check_license(hass, intrare)


def validate_license_result(result: LicenseResult) -> None:
    """Ridica eroare explicita pentru un rezultat de licenta nevalid."""

    if result.valid:
        return

    if result.connection_error:
        raise ValueError(result.message or "server_licenta_indisponibil")
    if result.status == LICENSE_STATUS_INVALID:
        raise ValueError("licenta_invalida")
    if result.status == LICENSE_STATUS_EXPIRED:
        raise ValueError("licenta_expirata")
    if result.status == LICENSE_STATUS_REVOKED:
        raise ValueError("licenta_revocata")
    if result.status == LICENSE_STATUS_INVALID_PRODUCT:
        raise ValueError("licenta_produs_invalid")
    if result.status == LICENSE_STATUS_ACTIVATION_LIMIT:
        raise ValueError("licenta_limita_activari")

    raise ValueError(result.message or "licenta_necunoscuta")


def valideaza_rezultat_licenta(rezultat: LicenseResult) -> None:
    """Alias in romana pentru validarea rezultatului de licenta."""

    validate_license_result(rezultat)


async def async_save_license_in_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    result: LicenseResult,
) -> None:
    """Salveaza rezultatul licentei si in config entry, pentru compatibilitate."""

    username, key, _storage = await async_get_license_context(hass, entry=entry)
    await async_save_global_license(hass, key, username, result)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, DATE_VERIFICARE_LICENTA: result.as_dict()},
    )


async def async_salveaza_licenta_in_intrare(
    hass: HomeAssistant,
    intrare: ConfigEntry,
    rezultat: LicenseResult,
) -> None:
    """Alias in romana pentru salvarea licentei in config entry."""

    await async_save_license_in_entry(hass, intrare, rezultat)


def _now_utc_iso() -> str:
    """Returneaza timpul curent UTC in format ISO."""

    return datetime.now(UTC).isoformat()


LICENSE_STATUS_LABELS: dict[str, str] = {
    LICENSE_STATUS_ACTIVE: "Activa",
    LICENSE_STATUS_TRIAL: "Trial activ",
    LICENSE_STATUS_EXPIRED: "Expirata",
    LICENSE_STATUS_INVALID: "Invalida",
    LICENSE_STATUS_REVOKED: "Revocata",
    LICENSE_STATUS_INVALID_PRODUCT: "Produs invalid",
    LICENSE_STATUS_ACTIVATION_LIMIT: "Limita activari atinsa",
    LICENSE_STATUS_UNKNOWN: "Necunoscuta",
}
