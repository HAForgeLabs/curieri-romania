"""Provider FAN Courier."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from zoneinfo import ZoneInfo
from typing import Any

from aiohttp import ClientError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    CONF_FAN_API_KEY,
    CONF_FAN_PASSWORD,
    CONF_FAN_USERNAME,
    COURIER_FAN,
)
from ..models import Parcel, ParcelDirection, ParcelEvent
from ..status import NormalizedStatus, normalize_fan_status
from .base import CourierProvider, CourierProviderError

_LOGGER = logging.getLogger(__name__)

FAN_API_BASE = "https://api.fancourier.ro"
FAN_API_KEY = "bih+NClaNylnd1FWVSEjOVwhentMLyxjP107cDVXRjVha011J3AiUXZEIkBeeCNVM1U+YEpObiR2I2R3LVBlYg=="
FAN_USER_AGENT = "okhttp/4.12.0"
FAN_LANGUAGE = "en"
FAN_PER_PAGE_ACTIVE = 15
FAN_PER_PAGE_HISTORY = 10
FAN_HISTORY_MONTHS = 6
FAN_MAX_DETAILS_PER_UPDATE = 30
FAN_TIMEZONE = ZoneInfo("Europe/Bucharest")


class FanCourierProvider(CourierProvider):
    """Provider FAN Courier folosind endpointurile mobile confirmate."""

    courier_code = COURIER_FAN
    display_name = "FAN Courier"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initializeaza providerul FAN Courier."""

        super().__init__(hass)
        self.entry = entry
        self._session = async_get_clientsession(hass)
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: datetime | None = None
        self._user_id: str | None = None
        self._client_ids: list[str] = []
        self.debug_info: dict[str, Any] = {}

    async def async_get_parcels(self) -> list[Parcel]:
        """Returneaza coletele FAN Courier active si istorice."""

        await self._async_login()
        if not self._token or not self._client_ids:
            raise CourierProviderError("Autentificare FAN Courier incompleta.")

        parcels: list[Parcel] = []
        errors: list[str] = []
        active_items: list[dict[str, Any]] = []
        registered_items: list[dict[str, Any]] = []
        history_items: list[dict[str, Any]] = []

        for client_id in self._client_ids:
            active_items.extend(
                await self._async_fetch_paginated_awbs(
                    "active",
                    client_id=client_id,
                    per_page=FAN_PER_PAGE_ACTIVE,
                    include_history_range=False,
                    errors=errors,
                )
            )
            registered_items.extend(
                await self._async_fetch_paginated_awbs(
                    "registered",
                    client_id=client_id,
                    per_page=FAN_PER_PAGE_ACTIVE,
                    include_history_range=False,
                    errors=errors,
                )
            )
            history_items.extend(
                await self._async_fetch_paginated_awbs(
                    "history",
                    client_id=client_id,
                    per_page=FAN_PER_PAGE_HISTORY,
                    include_history_range=True,
                    errors=errors,
                )
            )

        unique_items = _deduplicate_items([*active_items, *registered_items, *history_items])
        enriched_items = await self._async_enrich_items_with_details(unique_items, errors=errors)

        seen: set[str] = set()
        for item in enriched_items:
            parcel = self._parse_parcel(item)
            if parcel is None or parcel.unique_key in seen:
                continue
            seen.add(parcel.unique_key)
            parcels.append(parcel)

        self.debug_info.update(
            {
                "client_ids_count": len(self._client_ids),
                "active_items": len(active_items),
                "registered_items": len(registered_items),
                "history_items": len(history_items),
                "details_requested": min(len(unique_items), FAN_MAX_DETAILS_PER_UPDATE),
                "unique_parcels": len(parcels),
                "errors": errors,
                "token_expires_at": self._expires_at.isoformat() if self._expires_at else None,
            }
        )
        return parcels

    async def _async_login(self) -> None:
        """Autentifica providerul FAN Courier si salveaza tokenul in memorie."""

        if self._token and self._expires_at:
            now = datetime.now(timezone.utc)
            if self._expires_at > now + timedelta(minutes=5):
                return

        username = str(self.entry.data.get(CONF_FAN_USERNAME, "")).strip()
        password = str(self.entry.data.get(CONF_FAN_PASSWORD, ""))
        api_key = self._api_key

        if not username or not password or not api_key:
            raise CourierProviderError("FAN Courier nu este configurat complet.")

        try:
            async with self._session.post(
                f"{FAN_API_BASE}/mobile/v2/login",
                headers=self._headers(api_key=api_key, authorized=False),
                json={"username": username, "password": password},
            ) as response:
                if response.status in (400, 401, 403):
                    raise CourierProviderError("Autentificare FAN Courier esuata.")
                if response.status >= 400:
                    raise CourierProviderError(f"FAN Courier login HTTP {response.status}.")
                payload = await response.json(content_type=None)
        except ClientError as err:
            raise CourierProviderError("FAN Courier nu poate fi contactat momentan.") from err

        if payload.get("status") != "success":
            raise CourierProviderError("FAN Courier a respins autentificarea.")

        data = payload.get("data") or {}
        token = data.get("token")
        user_id = data.get("userId")
        if not token or user_id is None:
            raise CourierProviderError("FAN Courier nu a returnat token sau clientId.")

        self._token = str(token)
        self._refresh_token = str(data.get("refreshToken") or "") or None
        self._user_id = str(user_id)
        self._expires_at = _parse_fan_datetime(data.get("expiresAt"))

        client_ids, branch_count = await self._async_fetch_client_ids()
        if not client_ids:
            raise CourierProviderError("FAN Courier nu a returnat clientId valid.")
        self._client_ids = client_ids

        self.debug_info.update(
            {
                "login_status": "success",
                "user_type": data.get("type"),
                "validated_phone": data.get("validatedPhone"),
                "is_test_account": data.get("isTestAccount"),
                "expires_at_present": bool(data.get("expiresAt")),
                "branch_count": branch_count,
                "client_ids_count": len(self._client_ids),
                "client_id_source": "reports_mobile_branches",
            }
        )


    async def _async_fetch_client_ids(self) -> tuple[list[str], int]:
        """Obtine toate clientId-urile reale FAN Courier din lista de branch-uri.

        Raspunsul de login contine userId, dar endpointurile de AWB folosesc id-ul
        branch-ului/contului client. Pentru conturile cu mai multe branch-uri,
        interogam fiecare id valid ca sa nu ratam coletele asociate altor adrese.
        """

        if not self._token:
            raise CourierProviderError("Token FAN Courier lipsa.")

        api_key = self._api_key
        try:
            async with self._session.get(
                f"{FAN_API_BASE}/reports/mobile/branches",
                headers=self._headers(api_key=api_key, authorized=True),
            ) as response:
                if response.status in (401, 403):
                    self._token = None
                    raise CourierProviderError("Autentificare FAN Courier expirata. Reconecteaza contul.")
                if response.status >= 400:
                    raise CourierProviderError(f"FAN Courier branches HTTP {response.status}.")
                payload = await response.json(content_type=None)
        except ClientError as err:
            raise CourierProviderError("FAN Courier nu poate citi clientId-ul contului.") from err

        data = payload.get("data") if isinstance(payload, dict) else None
        if payload.get("status") != "success" or not isinstance(data, list):
            raise CourierProviderError("FAN Courier nu a returnat lista de conturi.")

        client_ids: list[str] = []
        seen: set[str] = set()
        for branch in data:
            if not isinstance(branch, dict):
                continue
            branch_id = branch.get("id")
            branch_text = str(branch_id).strip() if branch_id is not None else ""
            if branch_text and branch_text not in seen:
                seen.add(branch_text)
                client_ids.append(branch_text)

        return client_ids, len(data)

    async def _async_fetch_paginated_awbs(
        self,
        endpoint: str,
        *,
        client_id: str,
        per_page: int,
        include_history_range: bool,
        errors: list[str],
    ) -> list[dict[str, Any]]:
        """Citeste toate paginile unui endpoint AWB FAN Courier."""

        items: list[dict[str, Any]] = []
        total_pages = 1
        page = 1

        while page <= total_pages and page <= 10:
            try:
                payload = await self._async_get_awb_page(
                    endpoint,
                    client_id=client_id,
                    page=page,
                    per_page=per_page,
                    include_history_range=include_history_range,
                )
            except CourierProviderError as err:
                errors.append(f"{endpoint}:{err}")
                break

            data = payload.get("data") or {}
            page_items = data.get("items") or []
            if isinstance(page_items, list):
                for item in page_items:
                    if not isinstance(item, dict):
                        continue
                    item_copy = dict(item)
                    item_copy["__fan_client_id"] = client_id
                    item_copy["__fan_endpoint"] = endpoint
                    items.append(item_copy)

            total_pages = _safe_int(data.get("total"), default=1)
            if total_pages < 1:
                total_pages = 1
            page += 1

        return items

    async def _async_get_awb_page(
        self,
        endpoint: str,
        *,
        client_id: str,
        page: int,
        per_page: int,
        include_history_range: bool,
    ) -> dict[str, Any]:
        """Citeste o pagina din endpointurile AWB FAN Courier."""

        if not self._token or not client_id:
            raise CourierProviderError("Token sau clientId FAN Courier lipsa.")

        params: dict[str, Any] = {
            "clientId": client_id,
            "language": FAN_LANGUAGE,
            "page": page,
            "perPage": per_page,
        }

        if endpoint == "history":
            today = datetime.now().date()
            start = today - timedelta(days=31 * FAN_HISTORY_MONTHS)
            params["order"] = "desc"
            params["range[end]"] = today.isoformat()
            params["range[start]"] = start.isoformat()
        elif include_history_range:
            today = datetime.now().date()
            start = today - timedelta(days=31 * FAN_HISTORY_MONTHS)
            params["range[end]"] = today.isoformat()
            params["range[start]"] = start.isoformat()

        api_key = self._api_key
        try:
            async with self._session.get(
                f"{FAN_API_BASE}/mobile/v2/awb/{endpoint}",
                headers=self._headers(api_key=api_key, authorized=True),
                params=params,
            ) as response:
                if response.status in (401, 403):
                    self._token = None
                    raise CourierProviderError("Autentificare FAN Courier expirata. Reconecteaza contul.")
                if response.status >= 400:
                    raise CourierProviderError(f"HTTP {response.status}")
                payload = await response.json(content_type=None)
        except ClientError as err:
            raise CourierProviderError("FAN Courier nu poate fi interogat momentan.") from err

        if payload.get("status") != "success":
            raise CourierProviderError("raspuns fara status success")
        return payload

    async def _async_enrich_items_with_details(
        self,
        items: list[dict[str, Any]],
        *,
        errors: list[str],
    ) -> list[dict[str, Any]]:
        """Adauga detalii FAN Courier pentru primele colete unice.

        Endpointul de history returneaza doar evenimentul curent. Endpointul
        /mobile/v2/awb/details poate returna istoricul complet de evenimente.
        Limitam numarul de apeluri pe update ca sa nu incarcam inutil API-ul.
        """

        enriched: list[dict[str, Any]] = []
        detail_calls = 0

        for item in items:
            item_copy = dict(item)
            if detail_calls < FAN_MAX_DETAILS_PER_UPDATE:
                details = await self._async_fetch_awb_details(item_copy, errors=errors)
                if details:
                    item_copy["__fan_details"] = details
                detail_calls += 1
            enriched.append(item_copy)

        self.debug_info["details_calls"] = detail_calls
        return enriched

    def _record_detail_error(self, reason: str) -> None:
        """Inregistreaza o eroare optionala de details fara sa marcheze providerul ca esuat."""

        self.debug_info["details_errors"] = int(self.debug_info.get("details_errors", 0)) + 1
        self.debug_info["details_last_error"] = reason

    async def _async_fetch_awb_details(
        self,
        item: dict[str, Any],
        *,
        errors: list[str],
    ) -> dict[str, Any] | None:
        """Citeste detaliile unui AWB FAN Courier, fara sa blocheze update-ul."""

        if not self._token:
            return None

        awb = _as_text(item.get("awb") or item.get("awbNumber") or item.get("barcode"))
        client_id = _as_text(item.get("__fan_client_id"))
        if not awb or not client_id:
            return None

        params = {
            "awb": awb,
            "clientId": client_id,
            "flow": _as_text(item.get("flow")) or "incoming",
            "language": FAN_LANGUAGE,
            "type": _as_text(item.get("type")) or "finalized",
        }

        try:
            async with self._session.get(
                f"{FAN_API_BASE}/mobile/v2/awb/details",
                headers=self._headers(api_key=self._api_key, authorized=True),
                params=params,
            ) as response:
                if response.status in (401, 403):
                    self._token = None
                    self._record_detail_error("autentificare expirata")
                    return None
                if response.status >= 400:
                    self._record_detail_error(f"HTTP {response.status}")
                    return None
                payload = await response.json(content_type=None)
        except ClientError:
            self._record_detail_error("eroare retea")
            return None

        if not isinstance(payload, dict) or payload.get("status") != "success":
            self._record_detail_error("raspuns fara status success")
            return None

        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload


    @property
    def _api_key(self) -> str:
        """Returneaza cheia aplicatiei mobile FAN Courier.

        Cheia este comuna clientului mobil si poate fi suprascrisa din config entry
        pentru compatibilitate cu instalari de test mai vechi.
        """

        configured = str(self.entry.data.get(CONF_FAN_API_KEY, "")).strip()
        return configured or FAN_API_KEY

    def _headers(self, *, api_key: str, authorized: bool) -> dict[str, str]:
        """Construieste headerele FAN Courier fara logarea datelor sensibile."""

        headers = {
            "x-api-key": api_key,
            "accept": "application/json",
            "User-Agent": FAN_USER_AGENT,
        }
        if authorized and self._token:
            headers["authorization"] = f"Bearer {self._token}"
        return headers

    def _parse_parcel(self, item: dict[str, Any]) -> Parcel | None:
        """Mapeaza un item FAN Courier in modelul comun Parcel."""

        awb_value = item.get("awb") or item.get("awbNumber") or item.get("barcode")
        if awb_value is None:
            _LOGGER.debug("FAN Courier item ignorat fara AWB.")
            return None

        awb = str(awb_value).strip()
        if not awb:
            return None

        event = item.get("event") if isinstance(item.get("event"), dict) else {}
        details = item.get("__fan_details") if isinstance(item.get("__fan_details"), dict) else {}
        sender = _first_dict(item.get("sender"), details.get("sender"), details.get("expeditor"))
        recipient = _first_dict(item.get("recipient"), details.get("recipient"), details.get("receiver"), details.get("destinatar"))
        delivery_type = str(item.get("deliveryType") or details.get("deliveryType") or "").lower()
        flow = str(item.get("flow") or details.get("flow") or "").lower()
        detail_events = _extract_detail_events(details)
        event_models = _build_event_models(event, detail_events, delivery_type, _as_text(item.get("type")))

        original_status = _first_text(
            event.get("name"),
            event.get("description"),
            event.get("categoryName"),
            item.get("type"),
        )
        normalized_status = normalize_fan_status(
            event_id=_as_text(event.get("id")),
            category_id=_safe_int(event.get("categoryID")),
            event_name=_as_text(event.get("name")),
            event_description=_as_text(event.get("description")),
            category_name=_as_text(event.get("categoryName")),
            delivery_type=delivery_type,
            item_type=_as_text(item.get("type")),
        )
        last_update = _parse_fan_datetime(event.get("date"))
        if not original_status and event_models:
            original_status = event_models[-1].status
        if normalized_status == NormalizedStatus.UNKNOWN and event_models:
            normalized_status = event_models[-1].normalized_status
        if last_update is None and event_models:
            last_update = event_models[-1].event_time

        direction = ParcelDirection.UNKNOWN
        if flow == "incoming":
            direction = ParcelDirection.INCOMING
        elif flow in {"outgoing", "sent", "sender"}:
            direction = ParcelDirection.SENT

        recipient_location = _join_non_empty(recipient.get("locality"), recipient.get("address"))
        is_ooh = delivery_type == "ooh" or "fanbox" in recipient_location.lower()
        cod = _safe_float(item.get("value"))
        delivered_at = last_update if normalized_status == NormalizedStatus.DELIVERED else None

        return Parcel(
            awb=awb,
            courier=self.display_name,
            direction=direction,
            original_status=original_status,
            normalized_status=normalized_status,
            last_update=last_update,
            sender=_as_text(sender.get("name")),
            recipient=_as_text(recipient.get("name")),
            current_location=recipient_location or None,
            delivered_at=delivered_at,
            cash_on_delivery=cod,
            locker_name=recipient_location if is_ooh else None,
            is_locker=is_ooh,
            is_pickup_point=False,
            events=tuple(event_models),
            raw={
                "debug_source": {
                    "provider": "fan_courier",
                    "item_keys": sorted(key for key in item.keys() if not key.startswith("__fan_")),
                    "event_keys": sorted(event.keys()),
                    "details_present": bool(details),
                    "details_event_count": len(detail_events),
                    "delivery_type": delivery_type,
                    "flow": flow,
                }
            },
        )


def _deduplicate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplica itemurile FAN pastrand ordinea active -> registered -> history."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        awb = _as_text(item.get("awb") or item.get("awbNumber") or item.get("barcode"))
        if not awb:
            continue
        key = awb.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _first_dict(*values: Any) -> dict[str, Any]:
    """Returneaza primul dictionar valid."""

    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _extract_detail_events(details: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrage prudent lista de evenimente din raspunsul FAN details."""

    if not isinstance(details, dict):
        return []

    containers: list[dict[str, Any]] = [details]
    for key in ("awb", "details", "awbDetails", "shipment", "data"):
        value = details.get(key)
        if isinstance(value, dict):
            containers.append(value)

    for container in containers:
        for key in ("events", "history", "eventHistory", "awbHistory", "deliveryHistory", "tracking"):
            value = container.get(key)
            if isinstance(value, list):
                events = [event for event in value if isinstance(event, dict)]
                if events:
                    return events
    return []


def _build_event_models(
    current_event: dict[str, Any],
    detail_events: list[dict[str, Any]],
    delivery_type: str,
    item_type: str | None,
) -> list[ParcelEvent]:
    """Construieste evenimente FAN normalizate, cu fallback la evenimentul curent."""

    source_events = detail_events or ([current_event] if current_event else [])
    models: list[ParcelEvent] = []
    seen: set[tuple[str | None, datetime | None]] = set()

    for event in source_events:
        status = _first_text(
            event.get("name"),
            event.get("status"),
            event.get("statusName"),
            event.get("title"),
            event.get("description"),
            event.get("categoryName"),
        )
        event_time = _parse_fan_datetime(
            event.get("date")
            or event.get("eventDate")
            or event.get("createdAt")
            or event.get("time")
            or event.get("eventTime")
        )
        normalized = normalize_fan_status(
            event_id=_as_text(event.get("id") or event.get("eventId") or event.get("code")),
            category_id=_safe_int(event.get("categoryID") or event.get("categoryId")),
            event_name=_as_text(event.get("name") or event.get("status") or event.get("statusName") or event.get("title")),
            event_description=_as_text(event.get("description") or event.get("message")),
            category_name=_as_text(event.get("categoryName")),
            delivery_type=delivery_type,
            item_type=item_type,
        )
        key = (status, event_time)
        if key in seen:
            continue
        seen.add(key)
        models.append(
            ParcelEvent(
                status=status,
                normalized_status=normalized,
                status_id=_safe_int(event.get("categoryID") or event.get("categoryId")),
                location=_first_text(event.get("location"), event.get("locality"), event.get("city")),
                event_time=event_time,
                raw=_safe_event_debug(event),
            )
        )

    models.sort(key=lambda event: event.event_time or datetime.min.replace(tzinfo=FAN_TIMEZONE))
    return models


def _parse_fan_datetime(value: Any) -> datetime | None:
    """Parseaza datele FAN Courier in datetime timezone-aware.

    FAN Courier returneaza datele de eveniment in ora locala Romania, fara
    informatie de fus orar. Daca le-am marca drept UTC, Home Assistant ar
    adauga diferenta de fus orar si ar afisa ore deplasate.
    """

    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=FAN_TIMEZONE)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=FAN_TIMEZONE)
        return parsed
    except ValueError:
        return None


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


def _join_non_empty(*values: Any) -> str:
    """Concateneaza valori text neempty."""

    return ", ".join(text for value in values if (text := _as_text(value)))


def _safe_event_debug(event: dict[str, Any]) -> dict[str, Any]:
    """Pastreaza doar chei de status, fara date personale."""

    return {
        "id": event.get("id"),
        "categoryID": event.get("categoryID"),
        "name": event.get("name"),
        "categoryName": event.get("categoryName"),
        "date": event.get("date"),
    }
