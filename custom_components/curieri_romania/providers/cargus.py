"""Provider Cargus folosind portalul web MyCargus."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
import hashlib
import json
import logging
import time
import random
import uuid
from zoneinfo import ZoneInfo
from typing import Any

from aiohttp import ClientError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    CONF_CARGUS_ACCESS_TOKEN,
    CONF_CARGUS_EMAIL,
    CONF_CARGUS_PASSWORD,
    CONF_CARGUS_PHONE,
    CONF_CARGUS_REFRESH_TOKEN,
    CONF_CARGUS_REFRESH_TOKEN_EXPIRES_AT,
    CONF_CARGUS_TOKEN_EXPIRES_AT,
    COURIER_CARGUS,
)
from ..models import Parcel, ParcelDirection, ParcelEvent
from ..status import NormalizedStatus, normalize_cargus_status
from .base import CourierProvider, CourierProviderAuthError, CourierProviderError

_LOGGER = logging.getLogger(__name__)

CARGUS_API_BASE = "https://cmacwabackend.azurewebsites.net"
CARGUS_TOKEN_URL = (
    "https://myCargus.b2clogin.com/myCargus.onmicrosoft.com/"
    "B2C_1A_ACCOUNTLINK_SUSI_WEB/oauth2/v2.0/token"
)
CARGUS_CLIENT_ID = "0cbd5f1a-3ab9-40d6-b8ac-3c0ae62d23af"
CARGUS_SCOPE = (
    "https://myCargus.onmicrosoft.com/d12878e2-9602-447e-bd09-6e32e1d1d4bb/API.Access "
    "offline_access openid profile"
)
CARGUS_LANGUAGE = "RO"
CARGUS_SOURCE = "1"
CARGUS_TIMEZONE = ZoneInfo("Europe/Bucharest")
CARGUS_SHIPMENT_TYPES = (0, 1, 2, 3, 4)
CARGUS_MAX_DETAILS_PER_UPDATE = 30
CARGUS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"
)
CARGUS_TOKEN_REFRESH_MARGIN = 300
CARGUS_REFRESH_TOKEN_MARGIN = 900
CARGUS_REFRESH_TOKEN_FALLBACK_LIFETIME = 480
CARGUS_PARCEL_CACHE_SECONDS = 1800


class CargusProvider(CourierProvider):
    """Provider Cargus beta, cu refresh token din portalul MyCargus."""

    courier_code = COURIER_CARGUS
    display_name = "Cargus"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initializeaza providerul Cargus."""

        super().__init__(hass)
        self.entry = entry
        self._session = async_get_clientsession(hass)
        self._refresh_lock = asyncio.Lock()
        self._cached_parcels: list[Parcel] | None = None
        self._cached_parcels_at: float | None = None
        self.debug_info: dict[str, Any] = {}

    async def async_get_parcels(self) -> list[Parcel]:
        """Returneaza coletele Cargus din MyCargus."""

        phone = _normalize_phone(str(self.entry.data.get(CONF_CARGUS_PHONE, "")).strip())
        token = await self._async_get_access_token()
        if not phone or not token:
            raise CourierProviderError("Cargus nu este configurat complet.")

        now = time.time()
        if (
            self._cached_parcels is not None
            and self._cached_parcels_at is not None
            and now - self._cached_parcels_at < CARGUS_PARCEL_CACHE_SECONDS
        ):
            self.debug_info.update(
                {
                    "parcel_source": "cache",
                    "parcel_cache_age_seconds": round(now - self._cached_parcels_at),
                    "unique_parcels": len(self._cached_parcels),
                }
            )
            return list(self._cached_parcels)

        errors: list[str] = []
        items: list[dict[str, Any]] = []
        for shipment_type in CARGUS_SHIPMENT_TYPES:
            try:
                page_items = await self._async_fetch_awb_list(
                    shipment_type=shipment_type,
                    phone=phone,
                    token=token,
                )
            except CourierProviderError as err:
                errors.append(f"shipmentType={shipment_type}:{err}")
                continue
            for item in page_items:
                item_copy = dict(item)
                item_copy["__cargus_shipment_type"] = shipment_type
                items.append(item_copy)

        unique_items = _deduplicate_items(items)
        enriched_items = await self._async_enrich_items_with_details(
            unique_items,
            phone=phone,
            token=token,
            errors=errors,
        )

        parcels: list[Parcel] = []
        seen: set[str] = set()
        for item in enriched_items:
            parcel = self._parse_parcel(item)
            if parcel is None or parcel.unique_key in seen:
                continue
            seen.add(parcel.unique_key)
            parcels.append(parcel)

        self._cached_parcels = list(parcels)
        self._cached_parcels_at = time.time()
        self.debug_info.update(
            {
                "parcel_source": "api",
                "shipment_types": list(CARGUS_SHIPMENT_TYPES),
                "raw_items": len(items),
                "unique_items": len(unique_items),
                "details_requested": min(len(unique_items), CARGUS_MAX_DETAILS_PER_UPDATE),
                "unique_parcels": len(parcels),
                "errors": errors,
                "phone_format": "international" if phone.startswith("+") else "local",
                "token_present": bool(token),
                "refresh_token_present": bool(self.entry.data.get(CONF_CARGUS_REFRESH_TOKEN)),
            }
        )
        return parcels

    async def _async_get_access_token(self, *, force_refresh: bool = False) -> str:
        """Returneaza un access token valid si serializeaza refresh-urile Cargus."""

        async with self._refresh_lock:
            refresh_token = str(self.entry.data.get(CONF_CARGUS_REFRESH_TOKEN, "")).strip()
            cached_token = _normalize_access_token(
                str(self.entry.data.get(CONF_CARGUS_ACCESS_TOKEN, "")).strip()
            )
            expires_at = _safe_float(self.entry.data.get(CONF_CARGUS_TOKEN_EXPIRES_AT))
            refresh_expires_at = _safe_float(
                self.entry.data.get(CONF_CARGUS_REFRESH_TOKEN_EXPIRES_AT)
            )
            now = time.time()
            seconds_left = expires_at - now if expires_at else None
            refresh_seconds_left = (
                refresh_expires_at - now if refresh_expires_at else None
            )

            self.debug_info.update(
                {
                    "token_present": bool(cached_token),
                    "refresh_token_present": bool(refresh_token),
                    "token_expires_at_present": expires_at is not None,
                    "token_seconds_left": round(seconds_left) if seconds_left is not None else None,
                    "refresh_token_expires_at_present": refresh_expires_at is not None,
                    "refresh_token_seconds_left": (
                        round(refresh_seconds_left)
                        if refresh_seconds_left is not None
                        else None
                    ),
                    "token_refresh_forced": force_refresh,
                    "access_token_fingerprint_before": _token_fingerprint(cached_token),
                    "refresh_token_fingerprint_before": _token_fingerprint(refresh_token),
                }
            )

            access_token_safe = (
                not expires_at or expires_at > now + CARGUS_TOKEN_REFRESH_MARGIN
            )
            refresh_grant_safe = (
                not refresh_expires_at
                or refresh_expires_at > now + CARGUS_REFRESH_TOKEN_MARGIN
            )
            if refresh_expires_at and refresh_expires_at <= now + CARGUS_REFRESH_TOKEN_MARGIN:
                full_login_token = await self._async_full_login(reason="grant_absolute_expiry")
                if full_login_token:
                    return full_login_token

            if (
                not force_refresh
                and cached_token
                and access_token_safe
                and refresh_grant_safe
            ):
                self.debug_info["token_source"] = "cache"
                return cached_token

            if not refresh_token:
                # Compatibilitate cu intrarile vechi bazate pe access token manual.
                self.debug_info["token_source"] = "manual_access_token"
                return cached_token

            if force_refresh:
                reason = "forced_after_unauthorized"
            elif refresh_expires_at and not refresh_grant_safe:
                reason = "refresh_grant_margin"
            else:
                reason = "access_token_margin"
            self.debug_info["refresh_reason"] = reason
            _LOGGER.warning(
                "[CARGUS REFRESH DIAG] start reason=%s access_seconds_left=%s "
                "grant_seconds_left=%s forced=%s",
                reason,
                round(seconds_left) if seconds_left is not None else None,
                round(refresh_seconds_left) if refresh_seconds_left is not None else None,
                force_refresh,
            )
            try:
                payload = await _async_refresh_cargus_token(self._session, refresh_token)
            except CargusTokenRefreshError as err:
                self.debug_info.update(err.safe_debug)
                self.debug_info["refresh_success"] = False
                _LOGGER.warning(
                    "Cargus refresh esuat: status=%s error=%s description=%s",
                    err.safe_debug.get("refresh_http_status"),
                    err.safe_debug.get("refresh_error"),
                    err.safe_debug.get("refresh_error_description"),
                )
                full_login_token = await self._async_full_login(reason="refresh_failed")
                if full_login_token:
                    return full_login_token
                raise CourierProviderAuthError(
                    "Autentificare Cargus expirata. Reconecteaza contul."
                ) from err

            access_token = _normalize_access_token(str(payload.get("access_token", "")).strip())
            if not access_token:
                self.debug_info["refresh_success"] = False
                self.debug_info["refresh_error"] = "missing_access_token"
                raise CourierProviderAuthError(
                    "Autentificare Cargus expirata. Reconecteaza contul."
                )

            new_refresh_token = str(payload.get("refresh_token") or refresh_token).strip()
            expires_in = _safe_int(payload.get("expires_in"), 3600) or 3600
            declared_refresh_expires_in = _safe_int(payload.get("refresh_token_expires_in"))
            refresh_expires_in, refresh_expiry_source = _effective_refresh_lifetime(
                new_refresh_token, declared_refresh_expires_in
            )
            refreshed_at = time.time()
            access_expires_at = refreshed_at + expires_in
            refresh_token_expires_at = (
                refreshed_at + refresh_expires_in if refresh_expires_in else None
            )
            new_data = dict(self.entry.data)
            new_data[CONF_CARGUS_ACCESS_TOKEN] = access_token
            new_data[CONF_CARGUS_REFRESH_TOKEN] = new_refresh_token
            new_data[CONF_CARGUS_TOKEN_EXPIRES_AT] = access_expires_at
            if refresh_token_expires_at:
                new_data[CONF_CARGUS_REFRESH_TOKEN_EXPIRES_AT] = refresh_token_expires_at
            else:
                new_data.pop(CONF_CARGUS_REFRESH_TOKEN_EXPIRES_AT, None)
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)

            access_refresh_due_in = max(0, expires_in - CARGUS_TOKEN_REFRESH_MARGIN)
            grant_refresh_due_in = (
                max(0, refresh_expires_in - CARGUS_REFRESH_TOKEN_MARGIN)
                if refresh_expires_in
                else None
            )
            next_refresh_in = min(
                value
                for value in (access_refresh_due_in, grant_refresh_due_in)
                if value is not None
            )
            rotated = new_refresh_token != refresh_token
            self.debug_info.update(
                {
                    "token_source": "refresh",
                    "token_refreshed": True,
                    "refresh_success": True,
                    "refresh_http_status": 200,
                    "token_expires_in": expires_in,
                    "refresh_token_expires_in_declared": declared_refresh_expires_in,
                    "refresh_token_expires_in_effective": refresh_expires_in,
                    "refresh_token_expiry_source": refresh_expiry_source,
                    "refresh_not_before": payload.get("not_before"),
                    "refresh_token_rotated": rotated,
                    "next_refresh_in": next_refresh_in,
                    "access_token_fingerprint_after": _token_fingerprint(access_token),
                    "refresh_token_fingerprint_after": _token_fingerprint(new_refresh_token),
                }
            )
            _LOGGER.warning(
                "[CARGUS REFRESH DIAG] success reason=%s status=200 "
                "access_expires_in=%s grant_declared=%s grant_effective=%s "
                "grant_source=%s refresh_token_rotated=%s next_refresh_in=%s",
                reason,
                expires_in,
                declared_refresh_expires_in,
                refresh_expires_in,
                refresh_expiry_source,
                rotated,
                next_refresh_in,
            )
            return access_token


    async def _async_full_login(self, *, reason: str) -> str | None:
        """Obtine o familie noua de tokenuri folosind credentialele salvate."""
        email_value = str(self.entry.data.get(CONF_CARGUS_EMAIL, "")).strip()
        password = str(self.entry.data.get(CONF_CARGUS_PASSWORD, ""))
        if not email_value or not password:
            return None
        try:
            from ..config_flow import _sync_login_cargus_with_password
            token_data = await self.hass.async_add_executor_job(
                _sync_login_cargus_with_password, email_value, password
            )
        except Exception as err:  # autentificarea completa poate esua din motive externe
            _LOGGER.warning(
                "[CARGUS FULL LOGIN DIAG] failed reason=%s error_type=%s",
                reason, type(err).__name__,
            )
            return None
        access_token = _normalize_access_token(str(token_data.get(CONF_CARGUS_ACCESS_TOKEN, "")))
        refresh_token = str(token_data.get(CONF_CARGUS_REFRESH_TOKEN, "")).strip()
        if not access_token or not refresh_token:
            return None
        new_data = dict(self.entry.data)
        new_data.update(token_data)
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)
        _LOGGER.warning(
            "[CARGUS FULL LOGIN DIAG] success reason=%s token_rotated=True", reason
        )
        self.debug_info.update({
            "token_source": "full_login",
            "full_login_success": True,
            "full_login_reason": reason,
        })
        return access_token


    async def _async_fetch_awb_list(
        self,
        *,
        shipment_type: int,
        phone: str,
        token: str,
    ) -> list[dict[str, Any]]:
        """Citeste lista AWB Cargus pentru un shipmentType."""

        payload = await self._async_get_json(
            "/api/Awbs/AwbList",
            phone=phone,
            token=token,
            params={"shipmentType": shipment_type, "lang": CARGUS_LANGUAGE},
        )
        data = _extract_payload_data(payload)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    async def _async_enrich_items_with_details(
        self,
        items: list[dict[str, Any]],
        *,
        phone: str,
        token: str,
        errors: list[str],
    ) -> list[dict[str, Any]]:
        """Adauga detalii Cargus pentru primele colete unice."""

        enriched: list[dict[str, Any]] = []
        detail_calls = 0
        for item in items:
            item_copy = dict(item)
            if detail_calls < CARGUS_MAX_DETAILS_PER_UPDATE:
                details = await self._async_fetch_awb_details(item_copy, phone=phone, token=token)
                if details:
                    item_copy["__cargus_details"] = details
                detail_calls += 1
            enriched.append(item_copy)

        self.debug_info["details_calls"] = detail_calls
        return enriched

    async def _async_fetch_awb_details(
        self,
        item: dict[str, Any],
        *,
        phone: str,
        token: str,
    ) -> dict[str, Any] | None:
        """Citeste detalii pentru un AWB Cargus, fara sa blocheze update-ul."""

        awb = _as_text(item.get("awbBarCode") or item.get("awbBarcode") or item.get("barCode") or item.get("barcode"))
        if not awb:
            return None

        is_sender = bool(item.get("isSender"))
        try:
            payload = await self._async_get_json(
                "/api/Awbs/FullAwb",
                phone=phone,
                token=token,
                params={"BarCode": awb, "forSender": str(is_sender).lower(), "lang": CARGUS_LANGUAGE},
            )
        except CourierProviderError as err:
            self.debug_info["details_errors"] = int(self.debug_info.get("details_errors", 0)) + 1
            self.debug_info["details_last_error"] = str(err)
            return None

        data = _extract_payload_data(payload)
        if isinstance(data, dict):
            return data
        if isinstance(payload, dict):
            return payload
        return None

    async def _async_get_json(
        self,
        path: str,
        *,
        phone: str,
        token: str,
        params: dict[str, Any],
    ) -> Any:
        """Apeleaza API-ul Cargus si reincearca o data dupa 401/403."""

        request_token = _normalize_access_token(token)
        for attempt in range(2):
            status, payload = await self._async_request_json(
                path, phone=phone, token=request_token, params=params
            )
            self.debug_info["last_api_path"] = path
            self.debug_info["last_api_status"] = status
            self.debug_info["last_api_attempt"] = attempt + 1

            if status not in (401, 403):
                if status >= 400:
                    raise CourierProviderError(f"HTTP {status}")
                if attempt > 0:
                    self.debug_info["unauthorized_retry_success"] = True
                return payload

            self.debug_info["unauthorized_status"] = status
            self.debug_info["unauthorized_retry_attempted"] = attempt == 0
            if attempt > 0:
                self.debug_info["unauthorized_retry_success"] = False
                raise CourierProviderAuthError(
                    "Autentificare Cargus expirata. Reconecteaza contul."
                )

            latest_token = _normalize_access_token(
                str(self.entry.data.get(CONF_CARGUS_ACCESS_TOKEN, "")).strip()
            )
            if latest_token and latest_token != request_token:
                # Alt request a facut deja refresh. Refolosim tokenul nou fara alt refresh.
                request_token = latest_token
                self.debug_info["unauthorized_recovery"] = "reused_rotated_token"
            else:
                request_token = await self._async_get_access_token(force_refresh=True)
                self.debug_info["unauthorized_recovery"] = "forced_refresh"

        raise CourierProviderError("Cargus nu poate fi interogat momentan.")

    async def _async_request_json(
        self,
        path: str,
        *,
        phone: str,
        token: str,
        params: dict[str, Any],
    ) -> tuple[int, Any]:
        """Executa o singura cerere Cargus si returneaza statusul si payloadul."""

        headers = _build_headers(phone=phone, token=token)
        try:
            async with self._session.get(
                f"{CARGUS_API_BASE}{path}",
                headers=headers,
                params=params,
            ) as response:
                payload: Any = None
                if response.status < 400:
                    payload = await response.json(content_type=None)
                else:
                    # Corpul de eroare nu este pastrat pentru a evita date sensibile.
                    await response.read()
                return response.status, payload
        except ClientError as err:
            raise CourierProviderError("Cargus nu poate fi interogat momentan.") from err


    def _parse_parcel(self, item: dict[str, Any]) -> Parcel | None:
        """Mapeaza un item Cargus in modelul comun Parcel."""

        awb = _as_text(item.get("awbBarCode") or item.get("awbBarcode") or item.get("barCode") or item.get("barcode"))
        if not awb:
            _LOGGER.debug("Cargus item ignorat fara AWB.")
            return None

        details = item.get("__cargus_details") if isinstance(item.get("__cargus_details"), dict) else {}
        status = _first_text(
            item.get("status"),
            details.get("status"),
            details.get("statusMessage"),
            details.get("statusName"),
        )
        normalized_status = normalize_cargus_status(status=status, raw=item, details=details)
        last_update = _parse_cargus_datetime(
            item.get("date2")
            or item.get("date1")
            or details.get("date2")
            or details.get("date1")
            or details.get("deliveredAt")
        )
        delivered_at = last_update if normalized_status == NormalizedStatus.DELIVERED else None

        sender = _first_text(
            item.get("sender"),
            details.get("sender"),
            _dict_value(details.get("sender"), "name"),
            _dict_value(details.get("expeditor"), "name"),
        )
        recipient = _first_text(
            item.get("receiver"),
            details.get("receiver"),
            _dict_value(details.get("receiver"), "name"),
            _dict_value(details.get("recipient"), "name"),
        )
        location = _first_text(
            item.get("destination"),
            details.get("destination"),
            details.get("address"),
            _dict_value(details.get("receiver"), "address"),
            _dict_value(details.get("recipient"), "address"),
        )
        cash_on_delivery = _safe_float(
            item.get("amount")
            or item.get("cost")
            or item.get("value")
            or details.get("amount")
            or details.get("totalAmount")
        )
        delivery_type = _first_text(item.get("deliveryType"), details.get("deliveryType"), item.get("destinationTypeId"))
        is_sender = bool(item.get("isSender"))
        direction = ParcelDirection.SENT if is_sender else ParcelDirection.INCOMING
        if item.get("isSender") is None:
            direction = ParcelDirection.UNKNOWN

        events = _build_event_models(item, details, fallback_status=status, fallback_time=last_update)
        if events:
            final_event = events[-1]
            if normalized_status == NormalizedStatus.UNKNOWN:
                normalized_status = final_event.normalized_status
            if last_update is None:
                last_update = final_event.event_time
            if delivered_at is None and normalized_status == NormalizedStatus.DELIVERED:
                delivered_at = final_event.event_time

        is_locker = _looks_like_locker(location) or _looks_like_locker(delivery_type)

        return Parcel(
            awb=awb,
            courier=self.display_name,
            direction=direction,
            original_status=status,
            normalized_status=normalized_status,
            last_update=last_update,
            sender=sender,
            recipient=recipient,
            current_location=location,
            delivered_at=delivered_at,
            cash_on_delivery=cash_on_delivery,
            locker_name=location if is_locker else None,
            is_locker=is_locker,
            events=tuple(events),
            raw={
                "debug_source": {
                    "provider": "cargus",
                    "item_keys": sorted(key for key in item.keys() if not key.startswith("__cargus_")),
                    "details_present": bool(details),
                    "details_keys": sorted(details.keys()) if isinstance(details, dict) else [],
                    "details_event_count": len(events),
                    "shipment_type": item.get("__cargus_shipment_type"),
                    "delivery_type": delivery_type,
                }
            },
        )


def _effective_refresh_lifetime(
    refresh_token: str, declared_seconds: int | None
) -> tuple[int, str]:
    """Calculeaza conservator durata reala a grantului Cargus."""

    jwt_seconds = _jwt_seconds_until_expiry(refresh_token)
    candidates = [CARGUS_REFRESH_TOKEN_FALLBACK_LIFETIME]
    source = "fallback_cap"
    if declared_seconds and declared_seconds > 0:
        candidates.append(declared_seconds)
    if jwt_seconds and jwt_seconds > 0:
        candidates.append(jwt_seconds)
        source = "jwt_exp"
    effective = max(60, min(candidates))
    return effective, source


def _jwt_seconds_until_expiry(token: str) -> int | None:
    """Citeste local exp dintr-un JWT, fara validare si fara logarea tokenului."""

    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        segment = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(segment.encode("ascii")))
        exp = int(payload.get("exp"))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    seconds = exp - int(time.time())
    return seconds if seconds > 0 else None


class CargusTokenRefreshError(Exception):
    """Eroare refresh Cargus cu diagnostic sigur, fara tokenuri."""

    def __init__(self, message: str, safe_debug: dict[str, Any]) -> None:
        super().__init__(message)
        self.safe_debug = safe_debug


async def _async_refresh_cargus_token(session: Any, refresh_token: str) -> dict[str, Any]:
    """Schimba refresh tokenul Cargus si pastreaza doar diagnostic sigur."""

    data = {
        "client_id": CARGUS_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": CARGUS_SCOPE,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://mycargus.cargus.ro",
        "Referer": "https://mycargus.cargus.ro/",
        "User-Agent": CARGUS_USER_AGENT,
    }

    try:
        async with session.post(CARGUS_TOKEN_URL, data=data, headers=headers) as response:
            try:
                payload = await response.json(content_type=None)
            except (TypeError, ValueError):
                payload = {}

            if response.status >= 400:
                safe_debug = {
                    "refresh_http_status": response.status,
                    "refresh_error": _safe_debug_text(
                        payload.get("error") if isinstance(payload, dict) else None
                    ),
                    "refresh_error_description": _safe_debug_text(
                        payload.get("error_description") if isinstance(payload, dict) else None,
                        max_length=240,
                    ),
                    "refresh_response_keys": sorted(payload.keys())
                    if isinstance(payload, dict)
                    else [],
                }
                raise CargusTokenRefreshError(
                    f"Cargus token HTTP {response.status}", safe_debug
                )
    except ClientError as err:
        raise CourierProviderError(
            "Cargus nu poate improspata autentificarea momentan."
        ) from err

    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise CargusTokenRefreshError(
            "Cargus nu a returnat access token.",
            {
                "refresh_http_status": 200,
                "refresh_error": "missing_access_token",
                "refresh_response_keys": sorted(payload.keys())
                if isinstance(payload, dict)
                else [],
            },
        )
    return payload


def _build_headers(*, phone: str, token: str) -> dict[str, str]:
    """Construieste headerele Cargus cu K generat din telefon."""

    return {
        "Accept": "application/json",
        "Authorization": _normalize_access_token(token),
        "K": _generate_phone_key(phone),
        "Source": CARGUS_SOURCE,
        "Origin": "https://mycargus.cargus.ro",
        "Referer": "https://mycargus.cargus.ro/",
        "User-Agent": CARGUS_USER_AGENT,
    }


def _generate_phone_key(phone_number: str) -> str:
    """Genereaza headerul K Cargus dupa algoritmul PhoneKey din portalul web."""

    phone = phone_number.strip()
    had_plus = phone.startswith("+")
    if had_plus:
        phone = phone.replace("+", "", 1)

    if len(phone) < 7:
        # Backendul va respinge cheia, dar evitam exceptii locale.
        phone = phone.ljust(7, "0")

    ms_key = str(random.randint(0, 999) * 9 + 1750)[:4]
    ms_key = ms_key.ljust(4, "0")
    k0, k1, k2, k3 = [int(char) for char in ms_key[:4]]

    guid = uuid.uuid4().hex
    guid = guid + guid

    secret = "".join(
        [
            phone[0:1],
            str(k0),
            phone[1:3],
            str(k1),
            phone[3:5],
            str(k2),
            phone[5:7],
            str(k3),
            phone[7:],
        ]
    )
    secret = secret.ljust(20, "X") + ("Y" if had_plus else "X")

    mixed = ""
    for index, char in enumerate(secret):
        mixed += guid[index * 2 : index * 2 + 2] + char

    mixed = mixed[:6] + str(k2) + mixed[6:8] + str(k3) + mixed[8:]
    mixed = mixed[:k1] + str(k0) + mixed[k1:] + str(k1)
    return mixed


def _token_fingerprint(value: str) -> str | None:
    """Returneaza o amprenta scurta, nereversibila, pentru compararea tokenurilor."""

    token = value.strip()
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _safe_debug_text(value: Any, *, max_length: int = 120) -> str | None:
    """Curata un text de diagnostic si ii limiteaza lungimea."""

    text = _as_text(value)
    if not text:
        return None
    return " ".join(text.split())[:max_length]


def _normalize_access_token(value: str) -> str:
    """Normalizeaza tokenul Cargus pentru headerul Authorization."""

    token = value.strip()
    if not token:
        return ""
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def _normalize_phone(value: str) -> str:
    """Normalizeaza minimal telefonul, pastrand plusul daca exista."""

    return "".join(char for char in value.strip() if char.isdigit() or char == "+")


def _extract_payload_data(payload: Any) -> Any:
    """Extrage data din raspunsuri de tip dict sau returneaza payloadul brut."""

    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data")
    return payload


def _deduplicate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplica itemurile Cargus dupa AWB."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        awb = _as_text(item.get("awbBarCode") or item.get("awbBarcode") or item.get("barCode") or item.get("barcode"))
        if not awb:
            continue
        key = awb.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _build_event_models(
    item: dict[str, Any],
    details: dict[str, Any],
    *,
    fallback_status: str | None,
    fallback_time: datetime | None,
) -> list[ParcelEvent]:
    """Construieste evenimente Cargus din FullAwb sau fallback la statusul curent."""

    events = _extract_detail_events(details)
    if not events and fallback_status:
        events = [{"status": fallback_status, "date": fallback_time, "location": item.get("destination")}]

    models: list[ParcelEvent] = []
    seen: set[tuple[str | None, datetime | None]] = set()
    for event in events:
        status = _first_text(
            event.get("event"),
            event.get("status"),
            event.get("statusName"),
            event.get("name"),
            event.get("description"),
            event.get("message"),
        )
        event_time = _parse_cargus_datetime(
            event.get("date")
            or event.get("eventDate")
            or event.get("time")
            or event.get("eventTime")
            or event.get("createdAt")
        )
        normalized = normalize_cargus_status(status=status, raw=event, details=details)
        key = (status, event_time)
        if key in seen:
            continue
        seen.add(key)
        models.append(
            ParcelEvent(
                status=status,
                normalized_status=normalized,
                status_id=_safe_int(event.get("statusId") or event.get("statusID") or event.get("id")),
                location=_format_cargus_event_location(event),
                event_time=event_time,
                raw=_safe_event_debug(event),
            )
        )

    models.sort(key=lambda event: event.event_time or datetime.min.replace(tzinfo=CARGUS_TIMEZONE))
    return models


def _extract_detail_events(details: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrage prudent istoricul din FullAwb."""

    if not isinstance(details, dict):
        return []
    containers: list[dict[str, Any]] = [details]
    for key in ("awb", "details", "shipment", "data"):
        value = details.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for key in ("events", "history", "awbHistory", "deliveryHistory", "statuses", "tracking"):
            value = container.get(key)
            if isinstance(value, list):
                return [event for event in value if isinstance(event, dict)]
    return []


def _parse_cargus_datetime(value: Any) -> datetime | None:
    """Parseaza datele Cargus ca ora locala Romania daca lipseste timezone."""

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=CARGUS_TIMEZONE)
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    candidates = [text]
    if text.endswith("Z"):
        candidates.append(text.replace("Z", "+00:00"))
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=CARGUS_TIMEZONE)
            return parsed
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=CARGUS_TIMEZONE)
        except ValueError:
            continue
    return None


def _looks_like_locker(value: Any) -> bool:
    """Detecteaza livrarile la locker/punct Cargus."""

    text = str(value or "").lower()
    return any(word in text for word in ("locker", "ship & go", "ship&go", "punct", "pudo"))


def _safe_int(value: Any, default: int | None = None) -> int | None:
    """Converteste sigur o valoare in int."""

    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    """Converteste sigur o valoare in float."""

    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str | None:
    """Returneaza text curatat sau None."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(*values: Any) -> str | None:
    """Returneaza prima valoare text nevida."""

    for value in values:
        text = _as_text(value)
        if text:
            return text
    return None


def _dict_value(value: Any, key: str) -> Any:
    """Returneaza value[key] daca value este dict."""

    if isinstance(value, dict):
        return value.get(key)
    return None


def _format_cargus_event_location(event: dict[str, Any]) -> str | None:
    """Construieste locatia unui eveniment Cargus din campurile disponibile."""

    locality = _first_text(event.get("localityName"), event.get("locality"), event.get("city"))
    county = _first_text(event.get("countyName"), event.get("county"))
    if locality and county and locality.lower() != county.lower():
        return f"{locality}, {county}"
    return locality or county or _first_text(event.get("location"), event.get("address"))


def _safe_event_debug(event: dict[str, Any]) -> dict[str, Any]:
    """Pastreaza chei nesensibile din eveniment."""

    return {
        "id": event.get("id"),
        "statusId": event.get("statusId") or event.get("statusID"),
        "status": event.get("event") or event.get("status") or event.get("statusName") or event.get("name"),
        "date": event.get("date") or event.get("eventDate") or event.get("time"),
    }
