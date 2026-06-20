"""Provider Sameday, bazat pe fluxul web curat."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    CONF_SAMEDAY_ACCESS_TOKEN,
    CONF_SAMEDAY_REFRESH_TOKEN,
    CONF_SAMEDAY_TOKEN_EXPIRES_AT,
    CONF_SAMEDAY_TOKEN_TYPE,
    COURIER_SAMEDAY,
)
from ..models import Parcel, ParcelDirection, ParcelEvent
from ..status import NormalizedStatus, normalize_sameday_status
from .base import CourierProvider, CourierProviderError

_LOGGER = logging.getLogger(__name__)

SAMEDAY_IDENTITY_BASE = "https://identity.sameday.ro"
SAMEDAY_API_BASE = "https://recipients.sameday.ro"
SAMEDAY_API_VERSION = "2.0"
SAMEDAY_MAX_DETAILS_PER_UPDATE = 30

DASHBOARD_INCOMING_BUCKETS = ("active", "inLocker", "inPudo", "inDelivery")

SAMEDAY_WEB_CLIENT_ID = "Mobile_Web_Client"
SAMEDAY_WEB_REDIRECT_URI = "https://sameday.ro/trimite-colete/login-callback"
SAMEDAY_WEB_SCOPES = "openid profile roles offline_access IdentityServerApi"


class SamedayProvider(CourierProvider):
    """Provider Sameday."""

    courier_code = COURIER_SAMEDAY
    display_name = "Sameday"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initializeaza providerul Sameday."""

        super().__init__(hass)
        self._entry = entry
        self._session = async_get_clientsession(hass)
        self._debug_info: dict[str, Any] = {
            "provider": self.courier_code,
            "token_present": bool(entry.data.get(CONF_SAMEDAY_ACCESS_TOKEN)),
            "refresh_token_present": bool(entry.data.get(CONF_SAMEDAY_REFRESH_TOKEN)),
            "requests": [],
        }

    @property
    def debug_info(self) -> dict[str, Any]:
        """Returneaza date de diagnostic fara informatii sensibile."""

        return dict(self._debug_info)

    async def async_get_parcels(self) -> list[Parcel]:
        """Returneaza coletele Sameday."""

        self._debug_info = {
            "provider": self.courier_code,
            "token_present": bool(self._entry.data.get(CONF_SAMEDAY_ACCESS_TOKEN)),
            "refresh_token_present": bool(self._entry.data.get(CONF_SAMEDAY_REFRESH_TOKEN)),
            "requests": [],
        }

        if not self._entry.data.get(CONF_SAMEDAY_ACCESS_TOKEN):
            self._debug_info["skip_reason"] = "missing_access_token"
            return []

        dashboard = await self._async_get_json("/api/awbs?api-version=2.0")
        parcels: list[Parcel] = []
        seen_awbs: set[str] = set()

        dashboard_counts = {
            "active": len(_dashboard_items(dashboard.get("active"))),
            "c2c": len(_dashboard_items(dashboard.get("c2c"))),
            "history": dashboard.get("history") if isinstance(dashboard.get("history"), int) else None,
            "in_locker": len(_dashboard_items(dashboard.get("inLocker"))),
            "in_pudo": len(_dashboard_items(dashboard.get("inPudo"))),
            "in_delivery": len(_dashboard_items(dashboard.get("inDelivery"))),
            "in_locker_type": type(dashboard.get("inLocker")).__name__,
            "in_pudo_type": type(dashboard.get("inPudo")).__name__,
            "in_delivery_type": type(dashboard.get("inDelivery")).__name__,
        }
        self._debug_info["dashboard_counts"] = dashboard_counts
        self._debug_info["dashboard_keys"] = sorted(str(key) for key in dashboard.keys())

        skipped_without_awb = 0
        dashboard_parsed_by_bucket: dict[str, int] = {}
        dashboard_skipped_keys: dict[str, list[dict[str, Any]]] = {}
        for bucket in DASHBOARD_INCOMING_BUCKETS:
            parsed_count = 0
            for item in _dashboard_items(dashboard.get(bucket)):
                parcel = self._parse_dashboard_parcel(item, bucket=bucket)
                if parcel is None:
                    skipped_without_awb += 1
                    dashboard_skipped_keys.setdefault(bucket, []).append(_describe_item_shape(item))
                    continue
                if parcel.awb not in seen_awbs:
                    seen_awbs.add(parcel.awb)
                    parcels.append(parcel)
                    parsed_count += 1
            dashboard_parsed_by_bucket[bucket] = parsed_count

        if dashboard_skipped_keys:
            self._debug_info["dashboard_skipped_item_shapes"] = dashboard_skipped_keys

        for item in _dashboard_items(dashboard.get("c2c")):
            parcel = self._parse_dashboard_parcel(item, bucket="c2c", direction=ParcelDirection.SENT)
            if parcel is None:
                skipped_without_awb += 1
                continue
            if parcel.awb not in seen_awbs:
                seen_awbs.add(parcel.awb)
                parcels.append(parcel)
                dashboard_parsed_by_bucket["c2c"] = dashboard_parsed_by_bucket.get("c2c", 0) + 1

        self._debug_info["dashboard_parsed_by_bucket"] = dashboard_parsed_by_bucket

        dashboard_detail_success = 0
        dashboard_detail_errors = 0
        enriched_parcels: list[Parcel] = []
        for parcel in parcels:
            try:
                details = await self.async_get_details(parcel.awb)
            except CourierProviderError:
                dashboard_detail_errors += 1
                enriched_parcels.append(parcel)
                continue
            enriched_parcels.append(self._enrich_parcel_from_details(parcel, details))
            dashboard_detail_success += 1
        parcels = enriched_parcels
        self._debug_info["dashboard_details"] = {
            "requested": dashboard_detail_success + dashboard_detail_errors,
            "success": dashboard_detail_success,
            "errors": dashboard_detail_errors,
        }

        history_fetch_error: str | None = None
        history_items_count = 0
        history_total_count: int | None = None
        history_has_next: bool | None = None

        # Dashboardul Sameday poate intoarce doar un numar pentru "history".
        # In cazul acesta trebuie apelat explicit endpointul de istoric, altfel
        # contul pare gol desi exista colete livrate in portal.
        try:
            history = await self.async_get_history(page_number=1, page_size=20)
            history_items = _as_list(history.get("items"))
            history_items_count = len(history_items)
            history_total_count = _safe_int(history.get("totalCount"))
            history_has_next = _safe_bool(history.get("hasNext"))
            for item in history_items:
                if not isinstance(item, dict):
                    continue
                parcel = self._parse_history_parcel(item, direction=ParcelDirection.INCOMING)
                if parcel is None:
                    skipped_without_awb += 1
                    continue
                if parcel.awb not in seen_awbs:
                    seen_awbs.add(parcel.awb)
                    parcels.append(parcel)
        except CourierProviderError as err:
            history_fetch_error = str(err)

        sent_items_count = 0
        sent_total_count: int | None = None
        sent_fetch_error: str | None = None
        try:
            sent = await self.async_get_sent_c2c_history(page_number=1, page_size=20, history_type=0)
            sent_items = _as_list(sent.get("items"))
            sent_items_count = len(sent_items)
            sent_total_count = _safe_int(sent.get("totalCount"))
            for item in sent_items:
                if not isinstance(item, dict):
                    continue
                parcel = self._parse_history_parcel(item, direction=ParcelDirection.SENT)
                if parcel is None:
                    skipped_without_awb += 1
                    continue
                if parcel.awb not in seen_awbs:
                    seen_awbs.add(parcel.awb)
                    parcels.append(parcel)
        except CourierProviderError as err:
            sent_fetch_error = str(err)

        details_success = 0
        details_errors = 0
        details_skipped = 0
        detailed_parcels: list[Parcel] = []
        for parcel in parcels:
            if parcel.events:
                detailed_parcels.append(parcel)
                details_skipped += 1
                continue
            if details_success + details_errors >= SAMEDAY_MAX_DETAILS_PER_UPDATE:
                detailed_parcels.append(parcel)
                details_skipped += 1
                continue
            try:
                details = await self.async_get_details(parcel.awb)
            except CourierProviderError:
                details_errors += 1
                detailed_parcels.append(parcel)
                continue
            detailed_parcels.append(self._enrich_parcel_from_details(parcel, details))
            details_success += 1
        parcels = detailed_parcels
        self._debug_info["history_details"] = {
            "limit": SAMEDAY_MAX_DETAILS_PER_UPDATE,
            "success": details_success,
            "errors": details_errors,
            "skipped": details_skipped,
        }

        self._debug_info["history_counts"] = {
            "items": history_items_count,
            "total_count": history_total_count,
            "has_next": history_has_next,
            "error": history_fetch_error,
        }
        self._debug_info["sent_c2c_counts"] = {
            "items": sent_items_count,
            "total_count": sent_total_count,
            "error": sent_fetch_error,
        }
        self._debug_info["parsed_parcels"] = len(parcels)
        self._debug_info["skipped_without_awb"] = skipped_without_awb
        self._debug_info["normalized_status_counts"] = _count_statuses(parcels)

        _LOGGER.warning(
            "[Curieri Romania][Sameday DIAG] token_present=%s refresh_present=%s dashboard=%s history_items=%s sent_items=%s parsed=%s skipped_without_awb=%s errors=%s",
            self._debug_info.get("token_present"),
            self._debug_info.get("refresh_token_present"),
            dashboard_counts,
            history_items_count,
            sent_items_count,
            len(parcels),
            skipped_without_awb,
            self._debug_info.get("last_error"),
        )
        return parcels

    async def async_get_history(self, page_number: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Returneaza istoricul coletelor primite."""

        return await self._async_get_json(
            f"/api/awbs/history?pageNumber={page_number}&pageSize={page_size}&api-version={SAMEDAY_API_VERSION}"
        )

    async def async_get_sent_c2c_history(self, page_number: int = 1, page_size: int = 20, history_type: int = 0) -> dict[str, Any]:
        """Returneaza istoricul coletelor trimise C2C."""

        return await self._async_get_json(
            f"/api/awbs/history-senderC2C?type={history_type}&pageNumber={page_number}&pageSize={page_size}"
        )

    async def async_get_details(self, awb: str) -> dict[str, Any]:
        """Returneaza detaliile unui AWB."""

        return await self._async_get_json(f"/api/awbs/{awb}?api-version={SAMEDAY_API_VERSION}")

    async def _async_get_json(self, path: str) -> dict[str, Any]:
        """Executa un request GET catre API-ul Sameday."""

        await self._async_ensure_valid_token()
        access_token = self._entry.data.get(CONF_SAMEDAY_ACCESS_TOKEN)
        if not access_token:
            self._debug_info["last_error"] = "missing_access_token"
            raise CourierProviderError("Sameday nu este autentificat.")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "x-platform": "web",
        }
        request_debug: dict[str, Any] = {"path": _safe_debug_path(path)}
        try:
            async with self._session.get(f"{SAMEDAY_API_BASE}{path}", headers=headers) as response:
                request_debug["status"] = response.status
                if response.status == 401:
                    self._debug_info["last_error"] = "http_401"
                    raise CourierProviderError("Autentificare Sameday expirata. Reconecteaza contul.")
                if response.status >= 400:
                    text = await response.text()
                    request_debug["body_preview"] = _mask_text(text[:120])
                    self._debug_info["last_error"] = f"http_{response.status}"
                    raise CourierProviderError(f"Sameday a returnat HTTP {response.status}: {_mask_text(text[:120])}")
                data = await response.json(content_type=None)
        finally:
            self._debug_info.setdefault("requests", []).append(request_debug)

        if not isinstance(data, dict):
            self._debug_info["last_error"] = "invalid_response_type"
            raise CourierProviderError("Sameday a returnat un raspuns invalid.")

        request_debug["response_keys"] = sorted(str(key) for key in data.keys())
        return data

    async def _async_ensure_valid_token(self) -> None:
        """Reimprospateaza tokenul Sameday daca este aproape expirat."""

        expires_at = self._entry.data.get(CONF_SAMEDAY_TOKEN_EXPIRES_AT)
        self._debug_info["expires_at_present"] = expires_at is not None
        if not expires_at:
            return

        try:
            expires_at_float = float(expires_at)
        except (TypeError, ValueError):
            self._debug_info["token_expiry_valid"] = False
            return

        seconds_left = int(expires_at_float - time.time())
        self._debug_info["token_seconds_left"] = seconds_left
        if seconds_left > 300:
            return

        refresh_token = self._entry.data.get(CONF_SAMEDAY_REFRESH_TOKEN)
        if not refresh_token:
            self._debug_info["last_error"] = "missing_refresh_token"
            raise CourierProviderError("Autentificare Sameday expirata. Reconecteaza contul.")

        payload = {
            "grant_type": "refresh_token",
            "client_id": SAMEDAY_WEB_CLIENT_ID,
            "refresh_token": refresh_token,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}

        async with self._session.post(
            f"{SAMEDAY_IDENTITY_BASE}/connect/token",
            data=payload,
            headers=headers,
        ) as response:
            self._debug_info["refresh_status"] = response.status
            if response.status >= 400:
                self._debug_info["last_error"] = f"refresh_http_{response.status}"
                raise CourierProviderError("Autentificare Sameday expirata. Reconecteaza contul.")
            token_response = await response.json(content_type=None)

        expires_in = int(token_response.get("expires_in", 3600))
        new_data = dict(self._entry.data)
        new_data[CONF_SAMEDAY_ACCESS_TOKEN] = token_response.get("access_token")
        new_data[CONF_SAMEDAY_REFRESH_TOKEN] = token_response.get("refresh_token", refresh_token)
        new_data[CONF_SAMEDAY_TOKEN_TYPE] = token_response.get("token_type", "Bearer")
        new_data[CONF_SAMEDAY_TOKEN_EXPIRES_AT] = time.time() + expires_in
        self.hass.config_entries.async_update_entry(self._entry, data=new_data)
        self._debug_info["refresh_success"] = True

    def _parse_dashboard_parcel(
        self,
        item: dict[str, Any],
        *,
        bucket: str | None = None,
        direction: ParcelDirection = ParcelDirection.INCOMING,
    ) -> Parcel | None:
        """Parseaza un colet din dashboardul Sameday."""

        source = _find_parcel_source(item) or item
        awb = _extract_awb(source)
        if not awb:
            return None

        # Unele bucket-uri Sameday, mai ales inLocker/inPudo, au AWB-ul intr-un
        # obiect nested, dar datele de afisare raman pe obiectul parinte.
        # De aceea citim campurile din ambele surse: obiectul cu AWB si itemul
        # original din dashboard.
        sources = (source, item)

        current_status = _find_dict_value_any(sources, ("currentPublicStatus", "publicStatus", "statusDetails"))

        is_locker = _safe_bool(_find_value_any(sources, ("isLockerService", "isServiceLocker"))) or bucket == "inLocker"
        is_pudo = _safe_bool(_find_value_any(sources, ("isPudoService",))) or bucket == "inPudo"
        is_return = _safe_bool(_find_value_any(sources, ("isReturn", "isBackToSender", "inReturn")))

        original_status = (
            _safe_str(current_status.get("publicLabel"))
            or _safe_str(current_status.get("label"))
            or _safe_str(current_status.get("name"))
            or _first_text_any(sources, ("status", "statusState", "statusStateName", "cardState", "title"))
        )

        normalized_status = normalize_sameday_status(
            status_id=_safe_int(current_status.get("id")) or _safe_int(_find_value_any(sources, ("statusId",))),
            state_id=_safe_int(current_status.get("stateId")),
            status_state_id=_safe_int(_find_value_any(sources, ("statusStateId",))),
            status_name=_safe_str(current_status.get("name")) or _first_text_any(sources, ("status", "statusState", "statusStateName")),
            public_label=_safe_str(current_status.get("publicLabel")) or _safe_str(current_status.get("label")),
            is_locker=is_locker,
            is_pudo=is_pudo,
            is_return=is_return,
        )

        # Bucket-ul din dashboard este mai de incredere decat textul statusului
        # pentru coletele aflate deja in easybox/punct de ridicare. Sameday poate
        # trimite texte de forma "Colet incarcat in punctul de livrare", care
        # contin cuvantul "livrare", dar coletul este deja disponibil in locker.
        if bucket == "inLocker":
            normalized_status = NormalizedStatus.AVAILABLE_LOCKER
            original_status = original_status or "Incarcata in easybox"
        elif bucket == "inPudo":
            normalized_status = NormalizedStatus.AVAILABLE_PICKUP_POINT
            original_status = original_status or "Disponibil la punct de ridicare"
        elif bucket == "inDelivery":
            normalized_status = NormalizedStatus.OUT_FOR_DELIVERY
            original_status = original_status or "In livrare"

        locker_name = _first_text_any(sources, ("lockerName", "oohName", "pickupPointName", "locationName", "name"))
        locker_address = _first_text_any(sources, ("lockerAddress", "oohAddress", "pickupPointAddress", "address"))
        current_location = locker_name or locker_address or _first_text_any(sources, ("transitLocation", "county"))

        raw = _merge_public_raw(source, item)
        raw["debug_source"] = {
            "bucket": bucket,
            "item_shape": _describe_item_shape(item),
            "source_shape": _describe_item_shape(source),
            "source_is_nested": source is not item,
        }

        return Parcel(
            awb=awb,
            courier=self.courier_code,
            direction=direction,
            original_status=original_status,
            normalized_status=normalized_status,
            last_update=_parse_datetime(_find_value_any(sources, ("lastPublicUpdate", "statusDate", "updatedAt"))),
            pin_expiration_at=_parse_datetime(_find_value_any(sources, ("pinLockerExpirationDate", "pinExpirationDate"))),
            sender=_first_text_any(sources, ("senderName", "sender", "merchantName", "shopName")),
            current_location=current_location,
            estimated_delivery=_parse_datetime(_find_value_any(sources, ("deliveryEstimate", "estimatedDelivery", "deliveryIntervalStart"))),
            delivered_at=_parse_datetime(_find_value_any(sources, ("deliveredAt",))),
            cash_on_delivery=_safe_float(_find_value_any(sources, ("cashOnDelivery", "cod", "amount", "ramburs"))),
            locker_name=locker_name or locker_address,
            is_locker=is_locker,
            is_pickup_point=is_pudo,
            is_return=is_return,
            raw=raw,
        )


    def _enrich_parcel_from_details(self, parcel: Parcel, details: dict[str, Any]) -> Parcel:
        """Completeaza un colet activ cu detaliile din endpointul de AWB."""

        awb_active = details.get("awbActive")
        if not isinstance(awb_active, dict):
            return parcel

        events = self.parse_details_events(details)
        last_event = max(
            (event for event in events if event.event_time is not None),
            key=lambda event: event.event_time,
            default=None,
        )

        current_status = awb_active.get("currentPublicStatus")
        if not isinstance(current_status, dict):
            current_status = {}

        original_status = (
            _safe_str(current_status.get("publicLabel"))
            or _safe_str(current_status.get("label"))
            or _safe_str(current_status.get("name"))
            or parcel.original_status
        )

        normalized_status = normalize_sameday_status(
            status_id=_safe_int(current_status.get("id")),
            state_id=_safe_int(current_status.get("stateId")),
            status_name=_safe_str(current_status.get("name")),
            public_label=_safe_str(current_status.get("publicLabel")) or _safe_str(current_status.get("label")),
            is_locker=parcel.is_locker or _safe_bool(awb_active.get("isLockerService")) or _safe_bool(awb_active.get("isServiceLocker")),
            is_pudo=parcel.is_pickup_point or _safe_bool(awb_active.get("isPudoService")),
            is_return=parcel.is_return or _safe_bool(awb_active.get("isReturn")),
        )

        # Pastram bucket-ul initial ca sursa de adevar pentru coletele deja
        # identificate ca disponibile in locker/punct de ridicare.
        if parcel.normalized_status in {
            NormalizedStatus.AVAILABLE_LOCKER,
            NormalizedStatus.AVAILABLE_PICKUP_POINT,
        }:
            normalized_status = parcel.normalized_status

        locker_name = _safe_str(awb_active.get("lockerName")) or parcel.locker_name
        locker_address = _safe_str(awb_active.get("lockerAddress"))
        current_location = (
            locker_name
            or locker_address
            or _safe_str(awb_active.get("recipientAddress"))
            or (last_event.location if last_event else None)
            or parcel.current_location
        )

        raw = dict(parcel.raw)
        raw["details_loaded"] = True
        raw["details_keys"] = sorted(str(key) for key in awb_active.keys())

        return replace(
            parcel,
            original_status=original_status,
            normalized_status=normalized_status,
            last_update=parcel.last_update or (last_event.event_time if last_event else None),
            recipient=parcel.recipient or _safe_str(awb_active.get("recipientAddress")),
            current_location=current_location,
            estimated_delivery=parcel.estimated_delivery or _parse_datetime(awb_active.get("deliveryEstimate")),
            delivered_at=parcel.delivered_at or _parse_datetime(awb_active.get("deliveredAt")),
            pin_expiration_at=parcel.pin_expiration_at
            or _parse_datetime(awb_active.get("pinLockerExpirationDate") or awb_active.get("pinExpirationDate")),
            cash_on_delivery=parcel.cash_on_delivery
            if parcel.cash_on_delivery is not None
            else _safe_float(awb_active.get("cashOnDelivery")),
            locker_name=locker_name or locker_address,
            events=events or parcel.events,
            raw=raw,
        )


    def _parse_history_parcel(self, item: dict[str, Any], direction: ParcelDirection) -> Parcel | None:
        """Parseaza un colet din istoricul Sameday."""

        awb = _extract_awb(item)
        if not awb:
            return None

        current_status = item.get("currentPublicStatus")
        if not isinstance(current_status, dict):
            current_status = {}

        normalized_status = normalize_sameday_status(
            status_id=_safe_int(current_status.get("id")),
            state_id=_safe_int(current_status.get("stateId")),
            status_name=_safe_str(current_status.get("name")),
            public_label=_safe_str(current_status.get("publicLabel")) or _safe_str(current_status.get("label")),
            is_locker=_safe_bool(item.get("isLockerService")),
            is_pudo=_safe_bool(item.get("isPudoService")),
            is_return=_safe_bool(item.get("isReturn")),
            is_back_to_sender=_safe_bool(item.get("isBackToSender")),
        )

        return Parcel(
            awb=awb,
            courier=self.courier_code,
            direction=direction,
            original_status=_safe_str(current_status.get("publicLabel"))
            or _safe_str(current_status.get("label"))
            or _safe_str(current_status.get("name")),
            normalized_status=normalized_status,
            last_update=_parse_datetime(item.get("lastPublicUpdate")),
            sender=_safe_str(item.get("senderName")),
            current_location=_safe_str(item.get("lockerName")),
            delivered_at=_parse_datetime(item.get("deliveredAt")),
            pin_expiration_at=_parse_datetime(item.get("pinLockerExpirationDate") or item.get("pinExpirationDate")),
            cash_on_delivery=_safe_float(item.get("cashOnDelivery")),
            locker_name=_safe_str(item.get("lockerName")),
            is_locker=_safe_bool(item.get("isLockerService")),
            is_pickup_point=_safe_bool(item.get("isPudoService")),
            is_return=_safe_bool(item.get("isReturn")) or _safe_bool(item.get("isBackToSender")),
            raw=_safe_public_raw(item),
        )

    def parse_details_events(self, details: dict[str, Any]) -> tuple[ParcelEvent, ...]:
        """Parseaza evenimentele din endpointul de detalii Sameday."""

        awb_active = details.get("awbActive")
        if not isinstance(awb_active, dict):
            return ()

        events: list[ParcelEvent] = []
        for event in _as_list(awb_active.get("awbHistory")):
            if not isinstance(event, dict):
                continue
            status = _safe_str(event.get("status"))
            normalized = normalize_sameday_status(
                status_id=_safe_int(event.get("statusId")),
                state_id=_safe_int(event.get("statusStateId")),
                status_name=status,
            )
            events.append(
                ParcelEvent(
                    status=status,
                    normalized_status=normalized,
                    status_id=_safe_int(event.get("statusId")),
                    location=_safe_str(event.get("transitLocation")) or _safe_str(event.get("county")),
                    event_time=_parse_datetime(event.get("statusDate")),
                    raw=_safe_public_raw(event),
                )
            )
        return tuple(events)


def _as_list(value: Any) -> list[Any]:
    """Returneaza value daca este lista, altfel lista goala."""

    return value if isinstance(value, list) else []


def _dashboard_items(value: Any) -> list[dict[str, Any]]:
    """Extrage colete dintr-o sectiune de dashboard Sameday.

    API-ul poate returna sectiunile active/inLocker/inPudo/inDelivery fie ca lista,
    fie ca obiect unic, fie ca obiect cu camp items. Tratam toate variantele
    ca sa nu ignoram coletele ajunse in easybox.
    """

    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        nested_items = value.get("items")
        if isinstance(nested_items, list):
            return [item for item in nested_items if isinstance(item, dict)]
        if _safe_str(value.get("awbNumber")):
            return [value]
    return []


AWB_KEY_CANDIDATES = (
    "awbNumber",
    "awb",
    "awbNo",
    "awbNr",
    "awbCode",
    "awbBarCode",
    "awbBarcode",
    "barCode",
    "barcode",
    "parcelAwbNumber",
    "parcelAwb",
    "trackingNumber",
)


def _find_parcel_source(data: Any, depth: int = 0) -> dict[str, Any] | None:
    """Gaseste obiectul care contine AWB-ul real al coletului.

    Unele raspunsuri Sameday, mai ales inLocker/inPudo, impacheteaza
    coletul intr-un obiect intern. Daca luam campurile doar din obiectul
    parinte, pierdem statusul, expeditorul, rambursul si datele lockerului.
    """

    if depth > 5:
        return None

    if isinstance(data, dict):
        for key in AWB_KEY_CANDIDATES:
            candidate = _safe_str(data.get(key))
            if _looks_like_awb(candidate):
                return data

        for value in data.values():
            found = _find_parcel_source(value, depth + 1)
            if found is not None:
                return found

    if isinstance(data, list):
        for value in data:
            found = _find_parcel_source(value, depth + 1)
            if found is not None:
                return found

    return None


def _extract_awb(data: Any, depth: int = 0) -> str | None:
    """Extrage AWB-ul din structuri Sameday diferite, fara presupuneri fragile."""

    source = _find_parcel_source(data, depth)
    if source is None:
        return None

    for key in AWB_KEY_CANDIDATES:
        candidate = _safe_str(source.get(key))
        if _looks_like_awb(candidate):
            return candidate

    return None


def _find_value(data: Any, keys: tuple[str, ...], depth: int = 0) -> Any:
    """Cauta prima valoare disponibila pentru una dintre chei, inclusiv nested."""

    if depth > 5:
        return None

    if isinstance(data, dict):
        for key in keys:
            if key in data and data.get(key) is not None:
                return data.get(key)
        for value in data.values():
            found = _find_value(value, keys, depth + 1)
            if found is not None:
                return found

    if isinstance(data, list):
        for value in data:
            found = _find_value(value, keys, depth + 1)
            if found is not None:
                return found

    return None


def _find_value_any(sources: tuple[Any, ...], keys: tuple[str, ...]) -> Any:
    """Cauta prima valoare disponibila in mai multe surse."""

    for source in sources:
        found = _find_value(source, keys)
        if found is not None:
            return found
    return None


def _find_dict_value_any(sources: tuple[Any, ...], keys: tuple[str, ...]) -> dict[str, Any]:
    """Cauta primul obiect nested disponibil in mai multe surse."""

    value = _find_value_any(sources, keys)
    return value if isinstance(value, dict) else {}


def _first_text_any(sources: tuple[Any, ...], keys: tuple[str, ...]) -> str | None:
    """Cauta primul text disponibil in mai multe surse."""

    return _safe_str(_find_value_any(sources, keys))


def _find_dict_value(data: Any, keys: tuple[str, ...], depth: int = 0) -> dict[str, Any]:
    """Cauta un obiect nested pentru una dintre chei."""

    value = _find_value(data, keys, depth)
    return value if isinstance(value, dict) else {}


def _first_text(data: Any, keys: tuple[str, ...]) -> str | None:
    """Cauta un text in structura Sameday."""

    return _safe_str(_find_value(data, keys))


def _looks_like_awb(value: str | None) -> bool:
    """Valideaza prudent un posibil AWB fara a-l loga."""

    if not value:
        return False
    if len(value) < 8 or len(value) > 40:
        return False
    if any(ch.isspace() for ch in value):
        return False
    # AWB-urile Sameday observate sunt alfanumerice; acceptam si -/_ pentru prudenta.
    return all(ch.isalnum() or ch in {"-", "_"} for ch in value)


def _describe_item_shape(item: dict[str, Any]) -> dict[str, Any]:
    """Descrie structura unui item ignorat, fara valori personale."""

    shape: dict[str, Any] = {"keys": sorted(str(key) for key in item.keys())}
    nested: dict[str, list[str]] = {}
    for key, value in item.items():
        if isinstance(value, dict):
            nested[str(key)] = sorted(str(nested_key) for nested_key in value.keys())
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            nested[str(key)] = sorted(str(nested_key) for nested_key in value[0].keys())
    if nested:
        shape["nested_keys"] = nested
    return shape


def _safe_str(value: Any) -> str | None:
    """Converteste sigur la string."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: Any) -> int | None:
    """Converteste sigur la int."""

    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    """Converteste sigur la float."""

    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value: Any) -> bool:
    """Converteste sigur la bool."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes", "da"}
    return False


def _parse_datetime(value: Any) -> datetime | None:
    """Parseaza prudent un datetime ISO."""

    text = _safe_str(value)
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _safe_public_raw(data: dict[str, Any]) -> dict[str, Any]:
    """Pastreaza doar campuri utile si evita date personale evidente in raw."""

    allowed_keys = {
        "id",
        "currentPublicStatus",
        "lastPublicUpdate",
        "serviceId",
        "isReturn",
        "isLockerService",
        "isPudoService",
        "cardPaymentStatus",
        "status",
        "statusId",
        "statusState",
        "statusStateId",
        "statusStateName",
        "statusDate",
        "pinExpirationDate",
        "pinLockerExpirationDate",
        "lockerExtensionStatus",
        "canExtendDischargeDate",
        "numberOfAwbs",
    }
    return {key: value for key, value in data.items() if key in allowed_keys}


def _merge_public_raw(*sources: dict[str, Any]) -> dict[str, Any]:
    """Combina raw sigur din mai multe surse, pastrand prima valoare utila."""

    merged: dict[str, Any] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key, value in _safe_public_raw(source).items():
            if key not in merged or merged.get(key) is None:
                merged[key] = value
    return merged


def _safe_debug_path(path: str) -> str:
    """Mascheaza valori potential sensibile din path-uri de diagnostic."""

    safe_prefixes = (
        "/api/awbs?",
        "/api/awbs/history?",
        "/api/awbs/history-senderC2C?",
    )
    if path.startswith(safe_prefixes):
        return path

    if "/api/awbs/" not in path:
        return path
    prefix, _, suffix = path.partition("/api/awbs/")
    if not suffix:
        return path
    if "?" in suffix:
        _, query = suffix.split("?", 1)
        return f"{prefix}/api/awbs/***?{query}"
    return f"{prefix}/api/awbs/***"


def _mask_text(text: str) -> str:
    """Reduce riscul de expunere date personale in mesaje de eroare."""

    if not text:
        return text
    masked = text.replace("Bearer ", "Bearer ***")
    return masked[:120]


def _count_statuses(parcels: list[Parcel]) -> dict[str, int]:
    """Numara statusurile normalizate, fara AWB-uri."""

    counts: dict[str, int] = {}
    for parcel in parcels:
        key = str(parcel.normalized_status)
        counts[key] = counts.get(key, 0) + 1
    return counts
