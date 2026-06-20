"""Normalizare statusuri pentru colete."""

from __future__ import annotations

from enum import StrEnum


class NormalizedStatus(StrEnum):
    """Statusuri comune pentru toti curierii."""

    UNKNOWN = "necunoscut"
    REGISTERED = "inregistrat"
    PICKED_UP = "preluat"
    IN_TRANSIT = "in tranzit"
    IN_DEPOT = "in depozit"
    OUT_FOR_DELIVERY = "in livrare"
    AVAILABLE_LOCKER = "disponibil la locker"
    AVAILABLE_PICKUP_POINT = "disponibil la punct de ridicare"
    DELIVERED = "livrat"
    DELIVERY_FAILED = "livrare esuata"
    POSTPONED = "amanat"
    RETURNED = "returnat"
    CANCELLED = "anulat"
    PROBLEM = "problema"


_FINAL_STATUSES = {
    NormalizedStatus.DELIVERED,
    NormalizedStatus.RETURNED,
    NormalizedStatus.CANCELLED,
}

_PROBLEM_STATUSES = {
    NormalizedStatus.DELIVERY_FAILED,
    NormalizedStatus.PROBLEM,
    NormalizedStatus.RETURNED,
}


def is_final_status(status: NormalizedStatus) -> bool:
    """Returneaza True daca statusul este final."""

    return status in _FINAL_STATUSES


def is_problem_status(status: NormalizedStatus) -> bool:
    """Returneaza True daca statusul indica o problema."""

    return status in _PROBLEM_STATUSES


def normalize_sameday_status(
    *,
    status_id: int | None = None,
    state_id: int | None = None,
    status_state_id: int | None = None,
    status_name: str | None = None,
    public_label: str | None = None,
    is_locker: bool | None = None,
    is_pudo: bool | None = None,
    is_return: bool | None = None,
    is_back_to_sender: bool | None = None,
) -> NormalizedStatus:
    """Mapeaza statusurile Sameday catre statusurile comune.

    Maparea este intentionat prudenta. Pe masura ce strangem statusuri reale,
    completam numeric in functie de statusId/stateId/statusStateId.
    """

    if is_return or is_back_to_sender:
        return NormalizedStatus.RETURNED

    text = " ".join(
        str(value).lower()
        for value in (status_name, public_label)
        if value is not None
    )

    if any(word in text for word in ("livrat", "delivered")):
        return NormalizedStatus.DELIVERED
    if any(word in text for word in ("locker", "easybox")) and is_locker:
        return NormalizedStatus.AVAILABLE_LOCKER
    if any(word in text for word in ("pudo", "pickup", "ridicare")) and is_pudo:
        return NormalizedStatus.AVAILABLE_PICKUP_POINT
    if any(word in text for word in ("livrare", "delivery", "curier")):
        return NormalizedStatus.OUT_FOR_DELIVERY
    if any(word in text for word in ("depozit", "hub", "warehouse")):
        return NormalizedStatus.IN_DEPOT
    if any(word in text for word in ("tranzit", "transit")):
        return NormalizedStatus.IN_TRANSIT
    if any(word in text for word in ("preluat", "picked")):
        return NormalizedStatus.PICKED_UP
    if any(word in text for word in ("inregistrat", "registered", "created")):
        return NormalizedStatus.REGISTERED
    if any(word in text for word in ("esuat", "failed", "incident", "problema")):
        return NormalizedStatus.PROBLEM
    if any(word in text for word in ("amanat", "postponed")):
        return NormalizedStatus.POSTPONED
    if any(word in text for word in ("anulat", "cancelled", "canceled")):
        return NormalizedStatus.CANCELLED

    if status_id in {33} or state_id in {4} or status_state_id in {4}:
        return NormalizedStatus.OUT_FOR_DELIVERY
    if status_id in {46}:
        return NormalizedStatus.RETURNED
    if status_id in {74}:
        return NormalizedStatus.CANCELLED

    return NormalizedStatus.UNKNOWN


def normalize_fan_status(
    *,
    event_id: str | None = None,
    category_id: int | None = None,
    event_name: str | None = None,
    event_description: str | None = None,
    category_name: str | None = None,
    delivery_type: str | None = None,
    item_type: str | None = None,
) -> NormalizedStatus:
    """Mapeaza statusurile FAN Courier catre statusurile comune."""

    code = (event_id or "").strip().upper()
    text = " ".join(
        str(value).lower()
        for value in (event_name, event_description, category_name, delivery_type, item_type)
        if value is not None
    )

    if code == "S2" or category_id == 6 or "delivered" in text or "livrat" in text:
        return NormalizedStatus.DELIVERED
    if code == "C1" or "out for delivery" in text or "in livrare" in text:
        return NormalizedStatus.OUT_FOR_DELIVERY
    if code in {"H1"} or "destination hub" in text or "hub" in text or "depozit" in text:
        return NormalizedStatus.IN_DEPOT
    if code in {"H3", "H10"} or "transit" in text or "tranzit" in text:
        return NormalizedStatus.IN_TRANSIT
    if code == "S98" or "registered" in text or "inregistrat" in text:
        return NormalizedStatus.REGISTERED
    if "picked" in text or "preluat" in text:
        return NormalizedStatus.PICKED_UP
    if "fanbox" in text and any(word in text for word in ("available", "ready", "ridicare")):
        return NormalizedStatus.AVAILABLE_LOCKER
    if any(word in text for word in ("failed", "failure", "undelivered", "livrare esuata")):
        return NormalizedStatus.DELIVERY_FAILED
    if any(word in text for word in ("postponed", "amanat")):
        return NormalizedStatus.POSTPONED
    if any(word in text for word in ("return", "returned", "retur", "returnat")):
        return NormalizedStatus.RETURNED
    if any(word in text for word in ("cancel", "anulat")):
        return NormalizedStatus.CANCELLED
    if any(word in text for word in ("incident", "problem", "problema")):
        return NormalizedStatus.PROBLEM

    return NormalizedStatus.UNKNOWN


def normalize_cargus_status(
    *,
    status: str | None = None,
    raw: object | None = None,
    details: object | None = None,
) -> NormalizedStatus:
    """Mapeaza statusurile Cargus catre statusurile comune."""

    text_parts = [status]
    for source in (raw, details):
        if isinstance(source, dict):
            for key in (
                "status",
                "statusMessage",
                "statusName",
                "description",
                "message",
                "deliveryType",
                "destination",
            ):
                value = source.get(key)
                if value is not None:
                    text_parts.append(str(value))
    text = " ".join(part.lower() for part in text_parts if part)

    if any(word in text for word in ("livrat", "delivered", "predat")):
        return NormalizedStatus.DELIVERED
    if any(word in text for word in ("in livrare", "în livrare", "curier", "out for delivery")):
        return NormalizedStatus.OUT_FOR_DELIVERY
    if any(word in text for word in ("ship & go", "ship&go", "locker", "pudo")):
        return NormalizedStatus.AVAILABLE_PICKUP_POINT
    if any(word in text for word in ("depozit", "hub", "sortat", "consolidare", "sosit in", "sosit în", "a ajuns in", "a ajuns în")):
        return NormalizedStatus.IN_DEPOT
    if any(word in text for word in ("tranzit", "transit", "transport")):
        return NormalizedStatus.IN_TRANSIT
    if any(word in text for word in ("colectata", "colectată", "ridicat", "preluat", "picked")):
        return NormalizedStatus.PICKED_UP
    if any(word in text for word in ("inregistrat", "înregistrat", "creat", "awb creat", "created")):
        return NormalizedStatus.REGISTERED
    if any(word in text for word in ("esuat", "eșuat", "nereusit", "nereușit", "failed", "nelivrat")):
        return NormalizedStatus.DELIVERY_FAILED
    if any(word in text for word in ("amanat", "reprogram", "postponed")):
        return NormalizedStatus.POSTPONED
    if any(word in text for word in ("retur", "return", "returnat")):
        return NormalizedStatus.RETURNED
    if any(word in text for word in ("anulat", "cancel")):
        return NormalizedStatus.CANCELLED
    if any(word in text for word in ("incident", "problem", "problema", "bloc")):
        return NormalizedStatus.PROBLEM

    return NormalizedStatus.UNKNOWN


def normalize_gls_status(
    *,
    state: str | None = None,
    delivery_type: str | None = None,
    details_title: str | None = None,
    details_description: str | None = None,
    operation_description: str | None = None,
) -> NormalizedStatus:
    """Mapeaza statusurile GLS catre statusurile comune."""

    code = (state or "").strip().upper()
    delivery = (delivery_type or "").strip().upper()
    text = " ".join(
        str(value).lower()
        for value in (state, delivery_type, details_title, details_description, operation_description)
        if value is not None
    )

    if code == "DELIVERED":
        return NormalizedStatus.DELIVERED
    if code == "PICKED_UP":
        return NormalizedStatus.DELIVERED
    if code == "PICK_UP":
        if "LOCKER" in delivery or "locker" in text:
            return NormalizedStatus.AVAILABLE_LOCKER
        return NormalizedStatus.AVAILABLE_PICKUP_POINT
    if code == "IN_DELIVERY":
        return NormalizedStatus.OUT_FOR_DELIVERY
    if code == "SHIPPED":
        return NormalizedStatus.IN_TRANSIT
    if code == "PREPARING":
        return NormalizedStatus.REGISTERED
    if code == "FAILED":
        return NormalizedStatus.DELIVERY_FAILED
    if code == "DAMAGED":
        return NormalizedStatus.PROBLEM
    if code == "REFUSED":
        return NormalizedStatus.DELIVERY_FAILED
    if code == "CANCELLED":
        return NormalizedStatus.CANCELLED

    if any(word in text for word in ("delivered", "livrat", "picked up")):
        return NormalizedStatus.DELIVERED
    if any(word in text for word in ("parcellocker", "parcel locker", "locker")) and any(
        word in text for word in ("delivered into", "available", "pickup", "pick up", "ridicare")
    ):
        return NormalizedStatus.AVAILABLE_LOCKER
    if any(word in text for word in ("expected to be delivered", "during the day", "out for delivery", "curier")):
        return NormalizedStatus.OUT_FOR_DELIVERY
    if any(word in text for word in ("depot", "hub", "sorting center", "depozit", "sortare")):
        return NormalizedStatus.IN_DEPOT
    if any(word in text for word in ("transit", "tranzit", "transport")):
        return NormalizedStatus.IN_TRANSIT
    if any(word in text for word in ("driver has picked", "picked up the parcel", "preluat")):
        return NormalizedStatus.PICKED_UP
    if any(word in text for word in ("entered into the gls", "not yet handed", "inregistrat", "registered")):
        return NormalizedStatus.REGISTERED
    if any(word in text for word in ("failed", "refused", "undelivered", "livrare esuata")):
        return NormalizedStatus.DELIVERY_FAILED
    if any(word in text for word in ("return", "retur", "returnat")):
        return NormalizedStatus.RETURNED
    if any(word in text for word in ("cancel", "anulat")):
        return NormalizedStatus.CANCELLED
    if any(word in text for word in ("damaged", "problem", "incident", "problema")):
        return NormalizedStatus.PROBLEM

    return NormalizedStatus.UNKNOWN
