"""Integrarea Curieri Romania."""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import voluptuous as vol

from aiohttp import web

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.components.http import HomeAssistantView
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import dispatcher_send

from .const import (
    DOMAIN,
    NAME,
    PLATFORMS,
    SERVICE_REFRESH_LICENSE_STATUS,
    SERVICE_RESET_TEST_NOTIFICATION_HISTORY,
    SERVICE_SEND_TEST_NOTIFICATION,
    SERVICE_SIMULATE_NEW_PARCEL_NOTIFICATION,
    SERVICE_SIMULATE_STATUS_CHANGE_NOTIFICATION,
    SERVICE_UPDATE_NOTIFICATION_SETTINGS,
    SIGNAL_LICENSE_UPDATED,
    SIGNAL_NOTIFICATION_SETTINGS_UPDATED,
)
from .coordinator import CurieriRomaniaCoordinator

_LOGGER = logging.getLogger(__name__)
_PANEL_REGISTERED = False
_EXTERNAL_DONE_VIEW_REGISTERED = False
_TOKEN_RESULT_VIEW_REGISTERED = False
_PANEL_URL_PATH = "curieri_romania"
_PANEL_STATIC_URL = "/curieri_romania_static/curieri-romania-panel.js"
_HELPER_STATIC_PATHS = {
    "/curieri_romania_tools/cargus_refresh_token_helper.html": "tools/cargus_refresh_token_helper.html",
    "/curieri_romania_tools/sameday_refresh_token_helper.html": "tools/sameday_refresh_token_helper.html",
}
_ASSET_STATIC_PATHS = {
    "/curieri_romania_static/assets/curieri-romania-logo.png": "frontend/assets/curieri-romania-logo.png",
    "/curieri_romania_static/assets/haforge-logo.png": "frontend/assets/haforge-logo.png",
}


class CurieriRomaniaTokenResultView(HomeAssistantView):
    """Primeste tokenul extras de bookmarklet si continua flow-ul."""

    url = "/api/curieri_romania/token_result"
    name = "api:curieri_romania:token_result"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        """Salveaza tokenul trimis de bookmarklet prin formular POST."""

        hass: HomeAssistant = request.app["hass"]
        try:
            data = await request.post()
        except Exception:
            data = {}

        flow_id = str(data.get("flow_id", "")).strip()
        courier = str(data.get("courier", "")).strip()
        token = str(data.get("refresh_token", "")).strip()

        if not flow_id or not token:
            return web.Response(
                text=(
                    "<h1>Curieri Romania</h1>"
                    "<p>Tokenul nu a putut fi preluat. Revino in pagina curierului si ruleaza din nou helperul.</p>"
                ),
                content_type="text/html",
                status=400,
            )

        pending = hass.data.setdefault(DOMAIN, {}).setdefault("pending_helper_tokens", {})
        pending[flow_id] = {"courier": courier, "refresh_token": token}

        try:
            await hass.config_entries.flow.async_configure(flow_id, {"external_done": True})
        except Exception as err:  # pragma: no cover - protectie runtime HA
            _LOGGER.warning("Tokenul Curieri Romania a fost salvat, dar flow-ul nu a putut continua: %s", err)
            return web.Response(
                text=(
                    "<h1>Curieri Romania</h1>"
                    "<p>Tokenul a fost preluat, dar configurarea nu a putut continua automat. "
                    "Inchide aceasta fereastra si revino in Home Assistant.</p>"
                ),
                content_type="text/html",
                status=200,
            )

        return web.Response(
            text=(
                "<h1>Curieri Romania</h1>"
                "<p>Tokenul a fost preluat. Revino in Home Assistant; configurarea va continua automat.</p>"
                "<script>setTimeout(function(){ window.close(); }, 900);</script>"
            ),
            content_type="text/html",
        )


class CurieriRomaniaExternalStepDoneView(HomeAssistantView):
    """Marcheaza pasul extern din config flow ca finalizat."""

    url = "/api/curieri_romania/external_step_done"
    name = "api:curieri_romania:external_step_done"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Finalizeaza pasul extern si inchide fereastra helperului."""

        hass: HomeAssistant = request.app["hass"]
        flow_id = request.query.get("flow_id", "").strip()
        if not flow_id:
            return web.Response(
                text="<h1>Curieri Romania</h1><p>Lipseste flow_id.</p>",
                content_type="text/html",
                status=400,
            )

        try:
            result = await hass.config_entries.flow.async_configure(flow_id, {"external_done": True})
        except Exception as err:  # pragma: no cover - protectie runtime HA
            _LOGGER.warning("Nu s-a putut finaliza pasul extern Curieri Romania: %s", err)
            return web.Response(
                text="<h1>Curieri Romania</h1><p>Nu s-a putut continua configurarea. Inchide aceasta fereastra si revino in Home Assistant.</p>",
                content_type="text/html",
                status=500,
            )

        result_type = str(result.get("type", ""))
        if result_type not in {"external_done", "external_step_done", "form"}:
            _LOGGER.debug("Pas extern Curieri Romania finalizat cu rezultat neasteptat: %s", result)

        return web.Response(
            text=(
                "<h1>Curieri Romania</h1>"
                "<p>Pasul helper a fost finalizat. Revino in fereastra Home Assistant pentru a lipi refresh tokenul.</p>"
                "<script>setTimeout(function(){ window.close(); }, 500);</script>"
            ),
            content_type="text/html",
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configureaza integrarea dintr-un config entry."""

    await _async_register_dashboard_panel(hass)

    coordinator = CurieriRomaniaCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_license_services(hass, entry)
    _async_register_notification_services(hass)
    task = hass.async_create_task(_async_revalidate_license_non_blocking(hass, entry))
    entry.async_on_unload(task.cancel)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Descarca integrarea."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_register_dashboard_panel(hass: HomeAssistant) -> None:
    """Inregistreaza panelul dedicat Curieri Romania, o singura data."""

    global _PANEL_REGISTERED, _EXTERNAL_DONE_VIEW_REGISTERED, _TOKEN_RESULT_VIEW_REGISTERED

    if not _TOKEN_RESULT_VIEW_REGISTERED:
        hass.http.register_view(CurieriRomaniaTokenResultView())
        _TOKEN_RESULT_VIEW_REGISTERED = True

    if not _EXTERNAL_DONE_VIEW_REGISTERED:
        hass.http.register_view(CurieriRomaniaExternalStepDoneView())
        _EXTERNAL_DONE_VIEW_REGISTERED = True

    if _PANEL_REGISTERED:
        return

    panel_file = Path(__file__).parent / "frontend" / "curieri-romania-panel.js"
    if not panel_file.exists():
        _LOGGER.warning("Panelul Curieri Romania nu a fost gasit: %s", panel_file)
        return

    await _async_register_static_path(hass, _PANEL_STATIC_URL, str(panel_file))

    base_path = Path(__file__).parent
    for url_path, relative_path in _HELPER_STATIC_PATHS.items():
        helper_file = base_path / relative_path
        if helper_file.exists():
            await _async_register_static_path(hass, url_path, str(helper_file))
        else:
            _LOGGER.warning("Helperul Curieri Romania nu a fost gasit: %s", helper_file)

    for url_path, relative_path in _ASSET_STATIC_PATHS.items():
        asset_file = base_path / relative_path
        if asset_file.exists():
            await _async_register_static_path(hass, url_path, str(asset_file))
        else:
            _LOGGER.warning("Assetul Curieri Romania nu a fost gasit: %s", asset_file)

    try:
        from homeassistant.components import panel_custom

        result = panel_custom.async_register_panel(
            hass,
            webcomponent_name="curieri-romania-panel",
            frontend_url_path=_PANEL_URL_PATH,
            sidebar_title=NAME,
            sidebar_icon="mdi:truck-delivery-outline",
            module_url=_PANEL_STATIC_URL,
            config={"domain": DOMAIN},
            require_admin=False,
        )
        if inspect.isawaitable(result):
            await result
        _PANEL_REGISTERED = True
    except Exception as err:  # pragma: no cover - protectie compatibilitate HA
        _LOGGER.warning("Panelul Curieri Romania nu a putut fi inregistrat: %s", err)


async def _async_register_static_path(hass: HomeAssistant, url_path: str, file_path: str) -> None:
    """Inregistreaza fisierul frontend ca resursa statica."""

    try:
        from homeassistant.components.http import StaticPathConfig

        result = hass.http.async_register_static_paths(
            [StaticPathConfig(url_path, file_path, True)]
        )
        if inspect.isawaitable(result):
            await result
        return
    except Exception:
        # Compatibilitate cu versiuni mai vechi Home Assistant.
        pass

    try:
        result = hass.http.register_static_path(url_path, file_path, True)
        if inspect.isawaitable(result):
            await result
    except Exception as err:  # pragma: no cover - protectie compatibilitate HA
        _LOGGER.warning("Nu s-a putut inregistra resursa statica Curieri Romania: %s", err)


def _async_register_license_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Inregistreaza serviciul pentru actualizarea licentei, o singura data."""

    if hass.services.has_service(DOMAIN, SERVICE_REFRESH_LICENSE_STATUS):
        return

    async def async_refresh_license_status(call: ServiceCall) -> None:
        """Actualizeaza statusul licentei globale."""

        from .license import (
            async_obtine_context_licenta,
            async_salveaza_licenta_globala,
            async_valideaza_licenta,
        )

        username, license_key, _storage = await async_obtine_context_licenta(hass, intrare=entry)
        license_key = str(license_key or "").strip() or "TRIAL"
        result = await async_valideaza_licenta(hass, license_key, username)
        await async_salveaza_licenta_globala(hass, license_key, username, result)
        dispatcher_send(hass, SIGNAL_LICENSE_UPDATED)

        if not result.valid and result.connection_error:
            raise HomeAssistantError(result.message or "Serverul de licentiere nu a putut fi contactat.")

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_LICENSE_STATUS,
        async_refresh_license_status,
    )


def _async_register_notification_services(hass: HomeAssistant) -> None:
    """Inregistreaza serviciile pentru notificari, o singura data."""

    async def async_send_test_notification_service(call: ServiceCall) -> None:
        """Trimite o notificare simpla de test."""

        from .notify import async_send_test_notification

        await async_send_test_notification(hass)

    async def async_simulate_new_parcel_service(call: ServiceCall) -> None:
        """Simuleaza aparitia unui colet nou."""

        from .notify import async_simulate_new_parcel_notification

        await async_simulate_new_parcel_notification(hass)

    async def async_simulate_status_change_service(call: ServiceCall) -> None:
        """Simuleaza o schimbare de status."""

        from .notify import async_simulate_status_change_notification

        await async_simulate_status_change_notification(hass)

    async def async_reset_test_history_service(call: ServiceCall) -> None:
        """Reseteaza doar istoricul notificarilor de test."""

        from .notify import async_reset_test_notification_history

        await async_reset_test_notification_history(hass)

    async def async_update_notification_settings_service(call: ServiceCall) -> None:
        """Actualizeaza setarile reale pentru notificari."""

        from .storage import CurieriRomaniaNotificationSettingsStore

        updates = dict(call.data or {})
        await CurieriRomaniaNotificationSettingsStore(hass).async_update_settings(updates)
        dispatcher_send(hass, SIGNAL_NOTIFICATION_SETTINGS_UPDATED)

    services = {
        SERVICE_SEND_TEST_NOTIFICATION: (async_send_test_notification_service, None),
        SERVICE_SIMULATE_NEW_PARCEL_NOTIFICATION: (async_simulate_new_parcel_service, None),
        SERVICE_SIMULATE_STATUS_CHANGE_NOTIFICATION: (async_simulate_status_change_service, None),
        SERVICE_RESET_TEST_NOTIFICATION_HISTORY: (async_reset_test_history_service, None),
        SERVICE_UPDATE_NOTIFICATION_SETTINGS: (
            async_update_notification_settings_service,
            vol.Schema(
                {
                    vol.Optional("notifications_enabled"): bool,
                    vol.Optional("notify_service"): str,
                    vol.Optional("notify_new_parcel"): bool,
                    vol.Optional("notify_status_change"): bool,
                    vol.Optional("notify_out_for_delivery"): bool,
                    vol.Optional("notify_pickup"): bool,
                    vol.Optional("notify_delivered"): bool,
                    vol.Optional("notify_problems"): bool,
                    vol.Optional("notify_returned"): bool,
                }
            ),
        ),
    }

    for service_name, service_data in services.items():
        handler, schema = service_data
        if hass.services.has_service(DOMAIN, service_name):
            continue
        if schema is None:
            hass.services.async_register(DOMAIN, service_name, handler)
        else:
            hass.services.async_register(DOMAIN, service_name, handler, schema=schema)


async def _async_revalidate_license_non_blocking(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Revalideaza licenta dupa pornire fara sa blocheze integrarea."""

    try:
        from .license import (
            async_obtine_context_licenta,
            async_salveaza_licenta_globala,
            async_valideaza_licenta,
        )

        username, license_key, _storage = await async_obtine_context_licenta(hass, intrare=entry)
        license_key = str(license_key or "").strip() or "TRIAL"
        result = await async_valideaza_licenta(hass, license_key, username)

        if result.connection_error:
            _LOGGER.debug("Curieri Romania: revalidarea licentei nu a putut contacta serverul.")
            return

        await async_salveaza_licenta_globala(hass, license_key, username, result)
        dispatcher_send(hass, SIGNAL_LICENSE_UPDATED)
    except Exception as err:  # pragma: no cover - protectie runtime HA
        _LOGGER.debug("Curieri Romania: revalidarea licentei dupa pornire a esuat: %s", err)
