"""Modele comune pentru Curieri Romania."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .status import NormalizedStatus, is_final_status, is_problem_status


class ParcelDirection(StrEnum):
    """Directia coletului."""

    INCOMING = "incoming"
    SENT = "sent"
    UNKNOWN = "unknown"


@dataclass(slots=True, frozen=True)
class ParcelEvent:
    """Eveniment din istoricul unui colet."""

    status: str | None = None
    normalized_status: NormalizedStatus = NormalizedStatus.UNKNOWN
    status_id: int | None = None
    location: str | None = None
    event_time: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class Parcel:
    """Model comun pentru colet."""

    awb: str
    courier: str
    direction: ParcelDirection = ParcelDirection.UNKNOWN
    original_status: str | None = None
    normalized_status: NormalizedStatus = NormalizedStatus.UNKNOWN
    last_update: datetime | None = None
    sender: str | None = None
    recipient: str | None = None
    current_location: str | None = None
    estimated_delivery: datetime | None = None
    delivered_at: datetime | None = None
    pin_expiration_at: datetime | None = None
    cash_on_delivery: float | None = None
    locker_name: str | None = None
    locker_pin: str | None = None
    courier_phone: str | None = None
    delivery_time_window: str | None = None
    delivery_point_address: str | None = None
    is_locker: bool = False
    is_pickup_point: bool = False
    is_return: bool = False
    events: tuple[ParcelEvent, ...] = field(default_factory=tuple)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def unique_key(self) -> str:
        """Cheie stabila pentru colet."""

        return f"{self.courier}_{self.awb}".lower()

    @property
    def is_final(self) -> bool:
        """Returneaza True daca statusul este final."""

        return is_final_status(self.normalized_status)

    @property
    def has_problem(self) -> bool:
        """Returneaza True daca statusul indica o problema."""

        return is_problem_status(self.normalized_status)


@dataclass(slots=True, frozen=True)
class ParcelSnapshot:
    """Rezultatul unei actualizari complete."""

    parcels: tuple[Parcel, ...] = field(default_factory=tuple)
    updated_at: datetime | None = None
    errors: dict[str, str] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def active_count(self) -> int:
        """Numar colete active."""

        return sum(1 for parcel in self.parcels if not parcel.is_final)

    @property
    def problem_count(self) -> int:
        """Numar colete cu probleme."""

        return sum(1 for parcel in self.parcels if parcel.has_problem)

    @property
    def delivered_count(self) -> int:
        """Numar colete livrate."""

        return sum(
            1
            for parcel in self.parcels
            if parcel.normalized_status == NormalizedStatus.DELIVERED
        )
