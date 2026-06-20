"""Coordonator de update pentru Curieri Romania."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_CARGUS_ACCESS_TOKEN,
    CONF_CARGUS_PHONE,
    CONF_CARGUS_REFRESH_TOKEN,
    CONF_ENABLED_COURIERS,
    CONF_FAN_PASSWORD,
    CONF_FAN_USERNAME,
    CONF_GLS_ACCESS_TOKEN,
    CONF_GLS_REFRESH_TOKEN,
    CONF_SAMEDAY_ACCESS_TOKEN,
    COURIER_CARGUS,
    COURIER_FAN,
    COURIER_GLS,
    COURIER_SAMEDAY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .models import ParcelSnapshot
from .notify import async_handle_parcel_notifications
from .license_access import async_courier_allowed_by_license
from .providers import CargusProvider, CourierProvider, CourierProviderError, FanCourierProvider, GLSProvider, SamedayProvider

_LOGGER = logging.getLogger(__name__)


class CurieriRomaniaCoordinator(DataUpdateCoordinator[ParcelSnapshot]):
    """Coordonator pentru coletele agregate de la toti curierii."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initializeaza coordonatorul."""

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.entry = entry
        self.providers: list[CourierProvider] = self._build_providers(hass, entry)

    def _build_providers(self, hass: HomeAssistant, entry: ConfigEntry) -> list[CourierProvider]:
        """Construieste providerii activi."""

        providers: list[CourierProvider] = []
        enabled = entry.data.get(CONF_ENABLED_COURIERS, [COURIER_SAMEDAY])
        if COURIER_SAMEDAY in enabled and entry.data.get(CONF_SAMEDAY_ACCESS_TOKEN):
            providers.append(SamedayProvider(hass, entry))
        if (
            COURIER_FAN in enabled
            and entry.data.get(CONF_FAN_USERNAME)
            and entry.data.get(CONF_FAN_PASSWORD)
        ):
            providers.append(FanCourierProvider(hass, entry))
        if (
            COURIER_CARGUS in enabled
            and entry.data.get(CONF_CARGUS_PHONE)
            and (entry.data.get(CONF_CARGUS_REFRESH_TOKEN) or entry.data.get(CONF_CARGUS_ACCESS_TOKEN))
        ):
            providers.append(CargusProvider(hass, entry))
        if (
            COURIER_GLS in enabled
            and (entry.data.get(CONF_GLS_REFRESH_TOKEN) or entry.data.get(CONF_GLS_ACCESS_TOKEN))
        ):
            providers.append(GLSProvider(hass, entry))
        return providers

    async def _async_update_data(self) -> ParcelSnapshot:
        """Actualizeaza datele de la providerii activi."""

        parcels = []
        errors: dict[str, str] = {}
        debug: dict[str, Any] = {
            "entry_title": self.entry.title,
            "providers_configured": [provider.courier_code for provider in self.providers],
            "provider_count": len(self.providers),
            "sameday_token_present": bool(self.entry.data.get(CONF_SAMEDAY_ACCESS_TOKEN)),
            "fan_credentials_present": bool(
                self.entry.data.get(CONF_FAN_USERNAME)
                and self.entry.data.get(CONF_FAN_PASSWORD)
            ),
            "cargus_credentials_present": bool(
                self.entry.data.get(CONF_CARGUS_PHONE)
                and (self.entry.data.get(CONF_CARGUS_REFRESH_TOKEN) or self.entry.data.get(CONF_CARGUS_ACCESS_TOKEN))
            ),
            "gls_credentials_present": bool(
                self.entry.data.get(CONF_GLS_REFRESH_TOKEN) or self.entry.data.get(CONF_GLS_ACCESS_TOKEN)
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "providers": {},
        }

        if not await async_courier_allowed_by_license(self.hass, self.entry):
            debug["warning"] = "blocked_by_license"
            errors["license"] = "Curier dezactivat fara licenta activa. Prima intrare de curier ramane disponibila in modul gratuit."
            return ParcelSnapshot(
                parcels=tuple(),
                updated_at=datetime.now(timezone.utc),
                errors=errors,
                debug=debug,
            )

        if not self.providers:
            debug["warning"] = "no_active_provider"
            _LOGGER.warning(
                "[Curieri Romania][DIAG] entry=%s nu are niciun provider activ. sameday_token_present=%s",
                self.entry.title,
                debug["sameday_token_present"],
            )

        for provider in self.providers:
            try:
                provider_parcels = await provider.async_get_parcels()
                parcels.extend(provider_parcels)
                debug["providers"][provider.courier_code] = _provider_debug(provider, len(provider_parcels))
            except CourierProviderError as err:
                errors[provider.courier_code] = str(err)
                debug["providers"][provider.courier_code] = _provider_debug(provider, 0, str(err))
                _LOGGER.warning("Providerul %s a returnat eroare: %s", provider.courier_code, err)
            except Exception as err:  # pragma: no cover - protectie runtime HA
                errors[provider.courier_code] = "Eroare neasteptata. Vezi logurile Home Assistant."
                debug["providers"][provider.courier_code] = _provider_debug(provider, 0, f"unexpected:{type(err).__name__}")
                _LOGGER.exception("Eroare neasteptata la providerul %s", provider.courier_code)

        debug["total_parcels"] = len(parcels)
        debug["errors"] = errors
        _LOGGER.warning(
            "[Curieri Romania][DIAG] entry=%s providers=%s total_parcels=%s errors=%s",
            self.entry.title,
            debug["providers_configured"],
            len(parcels),
            errors,
        )

        snapshot = ParcelSnapshot(
            parcels=tuple(parcels),
            updated_at=datetime.now(timezone.utc),
            errors=errors,
            debug=debug,
        )

        try:
            await async_handle_parcel_notifications(self.hass, snapshot)
        except Exception as err:  # pragma: no cover - protectie runtime HA
            _LOGGER.debug("Curieri Romania: verificarea notificarilor a esuat: %s", err)

        return snapshot


def _provider_debug(provider: CourierProvider, parcel_count: int, error: str | None = None) -> dict[str, Any]:
    """Returneaza diagnostic sigur pentru un provider."""

    debug_info = getattr(provider, "debug_info", {})
    if not isinstance(debug_info, dict):
        debug_info = {}
    data: dict[str, Any] = dict(debug_info)
    data["parcel_count"] = parcel_count
    if error:
        data["error"] = error
    return data
