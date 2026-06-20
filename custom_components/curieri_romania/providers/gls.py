"""Provider GLS folosind aplicatia mobila GLS si token OAuth/MSAL."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any

from aiohttp import ClientError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    CONF_GLS_ACCESS_TOKEN,
    CONF_GLS_REFRESH_TOKEN,
    CONF_GLS_TOKEN_EXPIRES_AT,
    COURIER_GLS,
)
from ..models import Parcel, ParcelDirection, ParcelEvent
from ..status import NormalizedStatus, normalize_gls_status
from .base import CourierProvider, CourierProviderError

_LOGGER = logging.getLogger(__name__)

GLS_API_BASE = "https://api.gls-group.net/gls-loyalty-platform-ee-v0"
GLS_TOKEN_URL = (
    "https://login.gls-group.net/glsgroup.onmicrosoft.com/"
    "B2C_1A_SIGNUP_SIGNIN_EE/oauth2/v2.0/token"
)
GLS_CLIENT_ID = "35d654a4-76b7-414a-a219-adf92bfb5952"
GLS_SCOPE = (
    "https://glsgroup.onmicrosoft.com/35d654a4-76b7-414a-a219-adf92bfb5952/mobile.write "
    "https://glsgroup.onmicrosoft.com/35d654a4-76b7-414a-a219-adf92bfb5952/mobile.read "
    "offline_access openid profile"
)
GLS_APP_VERSION = "1.113.0"
GLS_APP_REGION = "RO"
GLS_PLATFORM = "ANDROID"
GLS_ACCEPT_LANGUAGE = "en"
GLS_TOKEN_REFRESH_MARGIN = 600
GLS_HISTORY_LIMIT = 60
GLS_HISTORY_DIRECTIONS = ("RECEIVER", "SENDER")
GLS_MAX_HISTORY_PAGES = 2


class GLSProvider(CourierProvider):
    """Provider GLS beta, cu refresh token extras din aplicatia mobila."""

    courier_code = COURIER_GLS
    display_name = "GLS"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initializeaza providerul GLS."""

        super().__init__(hass)
        self.entry = entry
        self._session = async_get_clientsession(hass)
        self.debug_info: dict[str, Any] = {}

    async def async_get_parcels(self) -> list[Parcel]:
        """Returneaza coletele GLS din contul mobil."""

        token = await self._async_get_access_token()
        if not token:
            raise CourierProviderError("GLS nu este configurat complet.")

        errors: list[str] = []
        raw_items: list[dict[str, Any]] = []

        try:
            raw_items.extend(await self._async_fetch_recently(token))
        except CourierProviderError as err:
            errors.append(f"recently:{err}")

        for direction in GLS_HISTORY_DIRECTIONS:
            try:
                raw_items.extend(await self._async_fetch_history(token, direction=direction))
            except CourierProviderError as err:
                errors.append(f"history:{direction}:{err}")

        unique_items = _deduplicate_items(raw_items)
        parcels: list[Parcel] = []
        seen: set[str] = set()
        for item in unique_items:
            parcel = self._parse_parcel(item)
            if parcel is None or parcel.unique_key in seen:
                continue
            seen.add(parcel.unique_key)
            parcels.append(parcel)

        self.debug_info.update(
            {
                "raw_items": len(raw_items),
                "unique_items": len(unique_items),
                "unique_parcels": len(parcels),
                "errors": errors,
                "token_present": bool(token),
                "refresh_token_present": bool(self.entry.data.get(CONF_GLS_REFRESH_TOKEN)),
                "history_limit": GLS_HISTORY_LIMIT,
                "history_directions": list(GLS_HISTORY_DIRECTIONS),
            }
        )
        return parcels

    async def _async_get_access_token(self) -> str:
        """Returneaza un access token GLS valid, folosind refresh tokenul salvat."""

        refresh_token = str(self.entry.data.get(CONF_GLS_REFRESH_TOKEN, "")).strip()
        cached_token = _normalize_access_token(str(self.entry.data.get(CONF_GLS_ACCESS_TOKEN, "")).strip())
        expires_at = _safe_float(self.entry.data.get(CONF_GLS_TOKEN_EXPIRES_AT))

        if cached_token and expires_at and expires_at > time.time() + GLS_TOKEN_REFRESH_MARGIN:
            return cached_token
        if not refresh_token:
            return cached_token

        payload = await async_refresh_gls_token(self._session, refresh_token)
        access_token = _normalize_access_token(str(payload.get("access_token", "")).strip())
        if not access_token:
            raise CourierProviderError("Autentificare GLS expirata. Reconecteaza contul.")

        new_refresh_token = str(payload.get("refresh_token") or refresh_token).strip()
        expires_in = _safe_int(payload.get("expires_in"), 86400) or 86400
        new_data = dict(self.entry.data)
        new_data[CONF_GLS_ACCESS_TOKEN] = access_token
        new_data[CONF_GLS_REFRESH_TOKEN] = new_refresh_token
        new_data[CONF_GLS_TOKEN_EXPIRES_AT] = time.time() + expires_in
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

        self.debug_info["token_refreshed"] = True
        self.debug_info["token_expires_in"] = expires_in
        return access_token

    async def _async_fetch_recently(self, token: str) -> list[dict[str, Any]]:
        """Citeste coletele active/recente GLS."""

        payload = await self._async_get_json(
            "/platform/v2/parcels/recently",
            token=token,
            params={},
        )
        if not isinstance(payload, dict):
            return []

        items: list[dict[str, Any]] = []
        for key, direction in (("receiverParcels", "RECEIVER"), ("senderParcels", "SENDER")):
            values = payload.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, dict):
                    item = dict(value)
                    item.setdefault("direction", direction)
                    item["__gls_source"] = "recently"
                    items.append(item)
        return items

    async def _async_fetch_history(self, token: str, *, direction: str) -> list[dict[str, Any]]:
        """Citeste istoricul GLS pentru directia ceruta."""

        items: list[dict[str, Any]] = []
        for page in range(GLS_MAX_HISTORY_PAGES):
            offset = page * GLS_HISTORY_LIMIT
            payload = await self._async_get_json(
                "/platform/v1/parcels/history",
                token=token,
                params={
                    "limit": GLS_HISTORY_LIMIT,
                    "offset": offset,
                    "parcelDirection": direction,
                },
            )
            if not isinstance(payload, dict):
                break
            entries = payload.get("entries")
            if not isinstance(entries, list) or not entries:
                break
            for entry in entries:
                if isinstance(entry, dict):
                    item = dict(entry)
                    item.setdefault("direction", direction)
                    item["__gls_source"] = f"history_{direction.lower()}"
                    items.append(item)
            total_count = _safe_int(payload.get("totalCount"), len(items)) or 0
            if offset + len(entries) >= total_count:
                break
        return items

    async def _async_get_json(
        self,
        path: str,
        *,
        token: str,
        params: dict[str, Any],
    ) -> Any:
        """Apeleaza API-ul GLS."""

        headers = _build_headers(token)
        try:
            async with self._session.get(
                f"{GLS_API_BASE}{path}",
                headers=headers,
                params=params,
            ) as response:
                if response.status in (401, 403):
                    raise CourierProviderError("Autentificare GLS expirata. Reconecteaza contul.")
                if response.status >= 400:
                    raise CourierProviderError(f"HTTP {response.status}")
                return await response.json(content_type=None)
        except ClientError as err:
            raise CourierProviderError("GLS nu poate fi interogat momentan. Se va reincerca automat.") from err

    def _parse_parcel(self, item: dict[str, Any]) -> Parcel | None:
        """Transforma un colet GLS in modelul comun."""

        awb = _as_text(item.get("displayNumber") or item.get("number") or item.get("internalId"))
        if not awb:
            _LOGGER.debug("GLS a returnat un colet fara AWB; este ignorat.")
            return None

        current_state = item.get("currentState") if isinstance(item.get("currentState"), dict) else {}
        state_wrapper = current_state.get("state") if isinstance(current_state.get("state"), dict) else {}
        state_code = _as_text(state_wrapper.get("state") or current_state.get("state"))
        details_title = _as_text(current_state.get("detailsTitle"))
        details_description = _as_text(current_state.get("detailsDescription"))
        delivery_type = _as_text(item.get("deliveryType"))
        last_update = _parse_datetime(state_wrapper.get("operationDateTime") or current_state.get("operationDateTime"))
        normalized = normalize_gls_status(
            state=state_code,
            delivery_type=delivery_type,
            details_title=details_title,
            details_description=details_description,
        )

        events = tuple(_parse_events(item.get("operations"), delivery_type=delivery_type))
        delivered_at = _delivered_at(events, normalized, last_update)
        direction = _parse_direction(item.get("direction"))

        home_details = item.get("homeDeliveryDetails") if isinstance(item.get("homeDeliveryDetails"), dict) else {}
        out_details = item.get("outOfHomeDeliveryDetails") if isinstance(item.get("outOfHomeDeliveryDetails"), dict) else {}
        delivery_point = out_details.get("deliveryPoint") if isinstance(out_details.get("deliveryPoint"), dict) else {}
        home_address = home_details.get("address") if isinstance(home_details.get("address"), dict) else {}
        point_address = delivery_point.get("address") if isinstance(delivery_point.get("address"), dict) else {}

        locker_name = _as_text(delivery_point.get("partnerName") or delivery_point.get("name") or point_address.get("name"))
        delivery_point_address = _format_address(point_address) or _format_address(home_address)
        current_location = locker_name or delivery_point_address or _as_text(home_address.get("city"))
        recipient = _as_text(home_address.get("name") or _nested_text(home_address, "contactInfo", "name"))
        courier_phone = _as_text(home_details.get("courierPhoneNumber"))
        delivery_time_window = _format_time_window(home_details.get("deliveryTimePeriod"))
        cash_on_delivery = _extract_payment_amount(item.get("paymentData"))
        is_locker = delivery_type == "PARCEL_LOCKER" or _as_text(delivery_point.get("deliveryPointType")) == "PARCEL_LOCKER"
        is_pickup_point = bool(delivery_point) and not is_locker

        raw = dict(item)
        raw["debug_source"] = item.get("__gls_source", "gls")
        raw["delivery_type"] = delivery_type
        raw["courier_phone"] = courier_phone
        raw["delivery_time_window"] = delivery_time_window
        raw["delivery_point_address"] = delivery_point_address

        return Parcel(
            awb=awb,
            courier=self.display_name,
            direction=direction,
            original_status=state_code or details_title,
            normalized_status=normalized,
            last_update=last_update,
            sender=_as_text(item.get("senderName")),
            recipient=recipient,
            current_location=current_location,
            delivered_at=delivered_at,
            cash_on_delivery=cash_on_delivery,
            locker_name=locker_name,
            locker_pin=_as_text(out_details.get("pin")),
            courier_phone=courier_phone,
            delivery_time_window=delivery_time_window,
            delivery_point_address=delivery_point_address,
            is_locker=is_locker,
            is_pickup_point=is_pickup_point,
            events=events,
            raw=raw,
        )


async def async_refresh_gls_token(session: Any, refresh_token: str) -> dict[str, Any]:
    """Regenereaza tokenul GLS din refresh token."""

    payload = {
        "client_id": GLS_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": GLS_SCOPE,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "x-app-name": "com.gls.loyalty.ro",
        "x-app-ver": GLS_APP_VERSION,
    }
    try:
        async with session.post(GLS_TOKEN_URL, data=payload, headers=headers) as response:
            if response.status in (400, 401, 403):
                raise CourierProviderError("Autentificare GLS esuata. Verifica refresh tokenul.")
            if response.status >= 400:
                raise CourierProviderError(f"GLS token HTTP {response.status}")
            token_payload = await response.json(content_type=None)
    except ClientError as err:
        raise CourierProviderError("GLS nu poate fi contactat momentan.") from err

    if not isinstance(token_payload, dict):
        raise CourierProviderError("Raspuns GLS token invalid.")
    return token_payload


def _build_headers(token: str) -> dict[str, str]:
    """Construieste header-ele pentru API-ul GLS."""

    return {
        "Authorization": f"Bearer {_normalize_access_token(token)}",
        "Accept": "application/json",
        "Accept-Charset": "UTF-8",
        "platform": GLS_PLATFORM,
        "appRegion": GLS_APP_REGION,
        "appVersion": GLS_APP_VERSION,
        "Accept-Language": GLS_ACCEPT_LANGUAGE,
        "User-Agent": "ktor-client",
    }


def _parse_events(values: Any, *, delivery_type: str | None) -> list[ParcelEvent]:
    """Parseaza evenimentele GLS."""

    if not isinstance(values, list):
        return []
    events: list[ParcelEvent] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        description = _as_text(value.get("description"))
        event_time = _parse_datetime(value.get("operationDateTime"))
        events.append(
            ParcelEvent(
                status=description,
                normalized_status=normalize_gls_status(
                    delivery_type=delivery_type,
                    operation_description=description,
                ),
                event_time=event_time,
                raw=dict(value),
            )
        )
    return tuple(events)


def _parse_direction(value: Any) -> ParcelDirection:
    """Mapeaza directia GLS."""

    text = _as_text(value).upper()
    if text == "RECEIVER":
        return ParcelDirection.INCOMING
    if text == "SENDER":
        return ParcelDirection.SENT
    return ParcelDirection.UNKNOWN


def _delivered_at(
    events: tuple[ParcelEvent, ...],
    normalized: NormalizedStatus,
    last_update: datetime | None,
) -> datetime | None:
    """Determina data livrarii, daca exista."""

    delivered_events = [
        event.event_time
        for event in events
        if event.normalized_status == NormalizedStatus.DELIVERED and event.event_time is not None
    ]
    if delivered_events:
        return max(delivered_events)
    if normalized == NormalizedStatus.DELIVERED:
        return last_update
    return None


def _extract_payment_amount(value: Any) -> float | None:
    """Extrage suma de plata/ramburs, daca exista."""

    if not isinstance(value, dict):
        return None
    fee = value.get("paymentFee")
    if not isinstance(fee, dict):
        return None
    amount = fee.get("amount")
    try:
        return float(amount)
    except (TypeError, ValueError):
        return None


def _format_time_window(value: Any) -> str | None:
    """Formateaza intervalul estimat de livrare GLS."""

    if not isinstance(value, dict):
        return None
    start = _trim_time(value.get("start"))
    end = _trim_time(value.get("end"))
    if start and end:
        return f"{start} - {end}"
    return start or end


def _trim_time(value: Any) -> str | None:
    """Reduce timpul la HH:MM."""

    text = _as_text(value)
    if not text:
        return None
    return text[:5] if len(text) >= 5 else text


def _format_address(value: Any) -> str | None:
    """Construieste o adresa scurta din structura GLS."""

    if not isinstance(value, dict):
        return None
    parts = []
    street = _as_text(value.get("street"))
    house_number = _as_text(value.get("houseNumber"))
    house_info = _as_text(value.get("houseNumberInfo"))
    city = _as_text(value.get("city"))
    zip_code = _as_text(value.get("zipCode"))
    if street:
        parts.append(" ".join(part for part in (street, house_number, house_info) if part).strip())
    if city:
        parts.append(city)
    if zip_code:
        parts.append(zip_code)
    return ", ".join(part for part in parts if part) or None


def _nested_text(data: dict[str, Any], key: str, nested_key: str) -> str | None:
    """Citeste text dintr-un sub-dictionar."""

    nested = data.get(key)
    if not isinstance(nested, dict):
        return None
    return _as_text(nested.get(nested_key))


def _deduplicate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Elimina duplicatele GLS pastrand primul item gasit."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = _as_text(item.get("internalId") or item.get("displayNumber") or item.get("number") or item.get("id"))
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _parse_datetime(value: Any) -> datetime | None:
    """Parseaza o data ISO GLS in UTC."""

    text = _as_text(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _normalize_access_token(value: str) -> str:
    """Elimina prefixul Bearer dintr-un access token."""

    token = value.strip()
    if token.lower().startswith("bearer "):
        return token[7:].strip()
    return token


def _safe_float(value: Any) -> float | None:
    """Converteste sigur la float."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int | None = None) -> int | None:
    """Converteste sigur la int."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_text(value: Any) -> str:
    """Returneaza text curatat."""

    if value is None:
        return ""
    return str(value).strip()
