"""Clase de baza pentru providerii de curieri."""

from __future__ import annotations

from abc import ABC, abstractmethod

from homeassistant.core import HomeAssistant

from ..models import Parcel


class CourierProviderError(Exception):
    """Eroare controlata ridicata de un provider de curier."""


class CourierProvider(ABC):
    """Interfata comuna pentru toti providerii."""

    courier_code: str
    display_name: str

    def __init__(self, hass: HomeAssistant) -> None:
        """Initializeaza providerul."""

        self.hass = hass

    @abstractmethod
    async def async_get_parcels(self) -> list[Parcel]:
        """Returneaza lista coletelor cunoscute."""
