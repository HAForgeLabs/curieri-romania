"""Config flow pentru Curieri Romania."""

from __future__ import annotations

import base64
import hashlib
import html
import logging
import re
import secrets
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url

from .const import (
    ADMIN_ENTRY_TITLE,
    ADMIN_UNIQUE_ID,
    CONF_COURIER,
    CONF_CARGUS_ACCESS_TOKEN,
    CONF_CARGUS_PHONE,
    CONF_CARGUS_REFRESH_TOKEN,
    CONF_CARGUS_TOKEN_EXPIRES_AT,
    CONF_ENABLED_COURIERS,
    CONF_ENTRY_TYPE,
    CONF_FAN_PASSWORD,
    CONF_FAN_USERNAME,
    CONF_GLS_ACCESS_TOKEN,
    CONF_GLS_REFRESH_TOKEN,
    CONF_GLS_TOKEN_EXPIRES_AT,
    CONF_INSTANCE_NAME,
    CONF_SAMEDAY_ACCESS_TOKEN,
    CONF_SAMEDAY_ID_TOKEN,
    CONF_SAMEDAY_REFRESH_TOKEN,
    CONF_SAMEDAY_SCOPE,
    CONF_SAMEDAY_TOKEN_EXPIRES_AT,
    CONF_SAMEDAY_TOKEN_TYPE,
    COURIER_CARGUS,
    COURIER_FAN,
    COURIER_GLS,
    COURIER_NAMES,
    COURIER_SAMEDAY,
    DOMAIN,
    ENTRY_TYPE_ADMIN,
    ENTRY_TYPE_COURIER,
    NAME,
    SUPPORTED_COURIERS,
)
from .providers.cargus import (
    CARGUS_API_BASE,
    CARGUS_CLIENT_ID,
    CARGUS_SCOPE,
    CARGUS_TOKEN_URL,
    _async_refresh_cargus_token,
    _build_headers,
    _normalize_phone,
)
from .providers.fan_courier import FAN_API_BASE, FAN_API_KEY, FAN_USER_AGENT
from .providers.gls import (
    GLS_API_BASE,
    GLS_CLIENT_ID,
    GLS_SCOPE,
    GLS_TOKEN_URL,
    async_refresh_gls_token,
    _build_headers as _build_gls_headers,
)
from .providers.sameday import (
    SAMEDAY_API_BASE,
    SAMEDAY_IDENTITY_BASE,
    SAMEDAY_WEB_CLIENT_ID,
    SAMEDAY_WEB_REDIRECT_URI,
    SAMEDAY_WEB_SCOPES,
)

FIELD_AUTHORIZATION_URL = "authorization_url"
FIELD_CALLBACK_URL = "callback_url"
FIELD_SAMEDAY_AUTH_METHOD = "sameday_auth_method"
FIELD_SAMEDAY_PHONE = "sameday_phone"
FIELD_SAMEDAY_PASSWORD = "sameday_password"
METHOD_SAMEDAY_PHONE_PASSWORD = "phone_password"
METHOD_SAMEDAY_REFRESH_TOKEN = "refresh_token"
METHOD_SAMEDAY_CALLBACK_URL = "callback_url"
FIELD_GLS_AUTH_METHOD = "gls_auth_method"
FIELD_GLS_EMAIL = "gls_email"
FIELD_GLS_PASSWORD = "gls_password"
METHOD_GLS_EMAIL_PASSWORD = "email_password"
METHOD_GLS_BROWSER = "browser"
METHOD_GLS_REFRESH_TOKEN = "refresh_token"
FIELD_CARGUS_AUTH_METHOD = "cargus_auth_method"
FIELD_CARGUS_EMAIL = "cargus_email"
FIELD_CARGUS_PASSWORD = "cargus_password"
METHOD_CARGUS_EMAIL_PASSWORD = "email_password"
METHOD_CARGUS_HELPER = "helper"
METHOD_CARGUS_REFRESH_TOKEN = "refresh_token"
CARGUS_POLICY = "B2C_1A_ACCOUNTLINK_SUSI_WEB"
CARGUS_TENANT = "myCargus.onmicrosoft.com"
CARGUS_HOST = "https://mycargus.b2clogin.com"
CARGUS_REDIRECT_URI = "https://mycargus.cargus.ro/authentication/login-callback"
CARGUS_AUTHORIZE_URL = f"{CARGUS_HOST}/{CARGUS_TENANT}/{CARGUS_POLICY}/oauth2/v2.0/authorize"
CARGUS_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
GLS_POLICY_UPPER = "B2C_1A_SIGNUP_SIGNIN_EE"
GLS_POLICY_LOWER = "B2C_1A_signup_signin_ee"
GLS_REDIRECT_URI = "msauth://com.gls.loyalty.RO/lQKV4PJ8ByAVcIYQV2QZ%2F1tnmhY%3D"
GLS_AUTHORIZE_URL = "https://login.gls-group.net/glsgroup.onmicrosoft.com/B2C_1A_SIGNUP_SIGNIN_EE/oAuth2/v2.0/authorize"
GLS_AUTH_SCOPE = (
    "https://glsgroup.onmicrosoft.com/35d654a4-76b7-414a-a219-adf92bfb5952/mobile.write "
    "openid offline_access profile "
    "https://glsgroup.onmicrosoft.com/35d654a4-76b7-414a-a219-adf92bfb5952/mobile.read"
)
GLS_BROWSER_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)
GLS_DALVIK_UA = "Dalvik/2.1.0 (Linux; U; Android 9; SM-S9260 Build/PQ3A.190705.05150936)"

SAMEDAY_HELPER_URL = "/curieri_romania_tools/sameday_refresh_token_helper.html"
CARGUS_HELPER_URL = "/curieri_romania_tools/cargus_refresh_token_helper.html"
EXTERNAL_STEP_DONE_URL = "/api/curieri_romania/external_step_done"

_LOGGER = logging.getLogger(__name__)


class CurieriRomaniaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configureaza administrarea si serviciile Curieri Romania din UI."""

    VERSION = 1

    def __init__(self) -> None:
        """Initializeaza flow-ul."""

        self._courier: str | None = None
        self._entry_title = NAME
        self._sameday_code_verifier: str | None = None
        self._sameday_state: str | None = None
        self._sameday_authorization_url: str | None = None
        self._cargus_phone: str | None = None
        self._default_sameday_refresh_token: str | None = None
        self._default_cargus_refresh_token: str | None = None
        self._gls_code_verifier: str | None = None
        self._gls_state: str | None = None
        self._gls_nonce: str | None = None
        self._gls_authorization_url: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Creeaza intai zona de administrare, apoi permite adaugarea serviciilor."""

        if not self._admin_entry_exists():
            return await self.async_step_admin(user_input)
        return await self.async_step_service(user_input)

    async def async_step_admin(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Instaleaza intrarea de administrare Curieri Romania."""

        if user_input is not None:
            await self.async_set_unique_id(ADMIN_UNIQUE_ID)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=ADMIN_ENTRY_TITLE,
                data={
                    CONF_ENTRY_TYPE: ENTRY_TYPE_ADMIN,
                    CONF_INSTANCE_NAME: ADMIN_ENTRY_TITLE,
                    CONF_ENABLED_COURIERS: [],
                },
            )

        return self.async_show_form(
            step_id="admin",
            data_schema=vol.Schema({}),
            errors={},
            description_placeholders={},
        )

    async def async_step_service(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Alege curierul/serviciul care trebuie adaugat."""

        errors: dict[str, str] = {}

        if user_input is not None:
            courier = str(user_input[CONF_COURIER])
            self._courier = courier
            self._entry_title = COURIER_NAMES.get(courier, NAME)

            if courier not in SUPPORTED_COURIERS:
                return await self.async_step_unsupported()

            await self.async_set_unique_id(f"{DOMAIN}_{courier}")
            self._abort_if_unique_id_configured()

            if courier == COURIER_SAMEDAY:
                return await self.async_step_sameday_auth_method()
            if courier == COURIER_FAN:
                return await self.async_step_fan_credentials()
            if courier == COURIER_CARGUS:
                return await self.async_step_cargus_auth_method()
            if courier == COURIER_GLS:
                return await self.async_step_gls_auth_method()

            errors[CONF_COURIER] = "unsupported_courier"

        schema = vol.Schema(
            {
                vol.Required(CONF_COURIER, default=COURIER_SAMEDAY): vol.In(
                    {
                        COURIER_SAMEDAY: COURIER_NAMES[COURIER_SAMEDAY],
                        COURIER_FAN: COURIER_NAMES[COURIER_FAN],
                        COURIER_CARGUS: COURIER_NAMES[COURIER_CARGUS],
                        COURIER_GLS: COURIER_NAMES[COURIER_GLS],
                    }
                ),
            }
        )
        return self.async_show_form(
            step_id="service",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )


    async def async_step_fan_credentials(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configureaza autentificarea FAN Courier."""

        errors: dict[str, str] = {}

        if user_input is not None:
            username = str(user_input.get(CONF_FAN_USERNAME, "")).strip()
            password = str(user_input.get(CONF_FAN_PASSWORD, ""))
            if not username:
                errors[CONF_FAN_USERNAME] = "required"
            if not password:
                errors[CONF_FAN_PASSWORD] = "required"

            if not errors:
                try:
                    await self._async_validate_fan_login(username, password, FAN_API_KEY)
                except ValueError as err:
                    errors["base"] = str(err)
                except Exception:  # pragma: no cover - protectie runtime HA
                    errors["base"] = "cannot_connect_fan"
                else:
                    data = {
                        CONF_ENTRY_TYPE: ENTRY_TYPE_COURIER,
                        CONF_COURIER: COURIER_FAN,
                        CONF_INSTANCE_NAME: self._entry_title,
                        CONF_ENABLED_COURIERS: [COURIER_FAN],
                        CONF_FAN_USERNAME: username,
                        CONF_FAN_PASSWORD: password,
                    }
                    return self.async_create_entry(title=self._entry_title, data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_FAN_USERNAME): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Required(CONF_FAN_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="fan_credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )



    async def async_step_cargus_auth_method(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Alege metoda de autentificare Cargus."""

        if user_input is not None:
            method = str(user_input.get(FIELD_CARGUS_AUTH_METHOD, METHOD_CARGUS_EMAIL_PASSWORD))
            if method == METHOD_CARGUS_HELPER:
                return await self.async_step_cargus_phone()
            if method == METHOD_CARGUS_REFRESH_TOKEN:
                return await self.async_step_cargus_credentials()
            return await self.async_step_cargus_login()

        schema = vol.Schema(
            {
                vol.Required(FIELD_CARGUS_AUTH_METHOD, default=METHOD_CARGUS_EMAIL_PASSWORD): vol.In(
                    {
                        METHOD_CARGUS_EMAIL_PASSWORD: "Email si parola Cargus (recomandat)",
                        METHOD_CARGUS_HELPER: "Helper Cargus / bookmarklet (avansat)",
                        METHOD_CARGUS_REFRESH_TOKEN: "Refresh token Cargus manual (avansat)",
                    }
                )
            }
        )
        return self.async_show_form(
            step_id="cargus_auth_method",
            data_schema=schema,
            errors={},
            description_placeholders={},
        )

    async def async_step_cargus_login(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Autentificare Cargus automata cu email/parola."""

        errors: dict[str, str] = {}
        default_phone = self._cargus_phone or ""

        if user_input is not None:
            phone = _normalize_phone(str(user_input.get(CONF_CARGUS_PHONE, "")).strip())
            email_value = str(user_input.get(FIELD_CARGUS_EMAIL, "")).strip()
            password = str(user_input.get(FIELD_CARGUS_PASSWORD, ""))
            self._cargus_phone = phone or self._cargus_phone

            if not phone:
                errors[CONF_CARGUS_PHONE] = "required"
            if not email_value:
                errors[FIELD_CARGUS_EMAIL] = "required"
            if not password:
                errors[FIELD_CARGUS_PASSWORD] = "required"

            if not errors:
                try:
                    token_data = await self._async_login_cargus_with_password(email_value, password)
                    validated_data = await self._async_validate_cargus_refresh_token(
                        phone,
                        str(token_data[CONF_CARGUS_REFRESH_TOKEN]),
                    )
                except ValueError as err:
                    errors["base"] = str(err)
                except Exception:  # pragma: no cover - protectie runtime HA
                    errors["base"] = "cannot_connect_cargus"
                else:
                    data = {
                        CONF_ENTRY_TYPE: ENTRY_TYPE_COURIER,
                        CONF_COURIER: COURIER_CARGUS,
                        CONF_INSTANCE_NAME: self._entry_title,
                        CONF_ENABLED_COURIERS: [COURIER_CARGUS],
                        CONF_CARGUS_PHONE: phone,
                        **validated_data,
                    }
                    return self.async_create_entry(title=self._entry_title, data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_CARGUS_PHONE, default=default_phone): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Required(FIELD_CARGUS_EMAIL): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Required(FIELD_CARGUS_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="cargus_login",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )

    async def async_step_cargus_phone(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Cere telefonul Cargus inainte de helperul extern."""

        errors: dict[str, str] = {}
        default_phone = self._cargus_phone or ""

        if user_input is not None:
            phone = _normalize_phone(str(user_input.get(CONF_CARGUS_PHONE, "")).strip())
            if not phone:
                errors[CONF_CARGUS_PHONE] = "required"
            else:
                self._cargus_phone = phone
                return await self.async_step_cargus_helper()

        schema = vol.Schema(
            {
                vol.Required(CONF_CARGUS_PHONE, default=default_phone): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
            }
        )
        return self.async_show_form(
            step_id="cargus_phone",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )

    async def async_step_cargus_helper(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Deschide helperul Cargus prin pas extern Home Assistant."""

        if user_input is not None:
            token = self._pop_pending_helper_token(COURIER_CARGUS)
            if token:
                self._default_cargus_refresh_token = token
                return await self._async_create_cargus_from_refresh_token(token)
            return self.async_external_step_done(next_step_id="cargus_credentials")

        return self.async_external_step(
            step_id="cargus_helper",
            url=self._build_helper_url(CARGUS_HELPER_URL, COURIER_CARGUS),
        )

    async def async_step_cargus_credentials(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configureaza autentificarea Cargus avansata cu refresh token manual."""

        errors: dict[str, str] = {}
        default_phone = self._cargus_phone or ""
        default_token = self._default_cargus_refresh_token or ""

        if user_input is not None:
            phone = _normalize_phone(str(user_input.get(CONF_CARGUS_PHONE, "")).strip())
            refresh_token = str(user_input.get(CONF_CARGUS_REFRESH_TOKEN, "")).strip()
            self._cargus_phone = phone or self._cargus_phone
            if not phone:
                errors[CONF_CARGUS_PHONE] = "required"
            if not refresh_token:
                errors[CONF_CARGUS_REFRESH_TOKEN] = "required"

            if not errors:
                result = await self._async_try_create_cargus_entry(phone, refresh_token)
                if result is not None:
                    return result
                errors["base"] = "invalid_auth_cargus"

        schema = vol.Schema(
            {
                vol.Required(CONF_CARGUS_PHONE, default=default_phone): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Required(CONF_CARGUS_REFRESH_TOKEN, default=default_token): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="cargus_credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={"helper_url": CARGUS_HELPER_URL},
        )

    async def async_step_gls_auth_method(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Alege metoda de autentificare GLS."""

        if user_input is not None:
            method = str(user_input.get(FIELD_GLS_AUTH_METHOD, METHOD_GLS_EMAIL_PASSWORD))
            if method == METHOD_GLS_REFRESH_TOKEN:
                return await self.async_step_gls_credentials()
            if method == METHOD_GLS_BROWSER:
                self._prepare_gls_authorization()
                return await self.async_step_gls_oauth()
            return await self.async_step_gls_login()

        schema = vol.Schema(
            {
                vol.Required(FIELD_GLS_AUTH_METHOD, default=METHOD_GLS_EMAIL_PASSWORD): vol.In(
                    {
                        METHOD_GLS_EMAIL_PASSWORD: "Email si parola GLS (recomandat)",
                        METHOD_GLS_REFRESH_TOKEN: "Refresh token GLS manual (avansat)",
                        METHOD_GLS_BROWSER: "Browser OAuth GLS (diagnostic)",
                    }
                )
            }
        )
        return self.async_show_form(
            step_id="gls_auth_method",
            data_schema=schema,
            errors={},
            description_placeholders={},
        )

    async def async_step_gls_login(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Autentificare GLS automata cu email/parola, fara mitmproxy."""

        errors: dict[str, str] = {}

        if user_input is not None:
            email_value = str(user_input.get(FIELD_GLS_EMAIL, "")).strip()
            password = str(user_input.get(FIELD_GLS_PASSWORD, ""))
            if not email_value:
                errors[FIELD_GLS_EMAIL] = "required"
            if not password:
                errors[FIELD_GLS_PASSWORD] = "required"

            if not errors:
                try:
                    token_data = await self._async_login_gls_with_password(email_value, password)
                except ValueError as err:
                    errors["base"] = str(err)
                except Exception:  # pragma: no cover - protectie runtime HA
                    errors["base"] = "cannot_connect_gls"
                else:
                    data = {
                        CONF_ENTRY_TYPE: ENTRY_TYPE_COURIER,
                        CONF_COURIER: COURIER_GLS,
                        CONF_INSTANCE_NAME: self._entry_title,
                        CONF_ENABLED_COURIERS: [COURIER_GLS],
                        **token_data,
                    }
                    return self.async_create_entry(title=self._entry_title, data=data)

        schema = vol.Schema(
            {
                vol.Required(FIELD_GLS_EMAIL): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Required(FIELD_GLS_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="gls_login",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )

    async def async_step_gls_credentials(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configureaza autentificarea GLS beta cu refresh token manual."""

        errors: dict[str, str] = {}

        if user_input is not None:
            refresh_token = str(user_input.get(CONF_GLS_REFRESH_TOKEN, "")).strip()
            if not refresh_token:
                errors[CONF_GLS_REFRESH_TOKEN] = "required"

            if not errors:
                try:
                    token_data = await self._async_validate_gls_refresh_token(refresh_token)
                except ValueError as err:
                    errors["base"] = str(err)
                except Exception:  # pragma: no cover - protectie runtime HA
                    errors["base"] = "cannot_connect_gls"
                else:
                    data = {
                        CONF_ENTRY_TYPE: ENTRY_TYPE_COURIER,
                        CONF_COURIER: COURIER_GLS,
                        CONF_INSTANCE_NAME: self._entry_title,
                        CONF_ENABLED_COURIERS: [COURIER_GLS],
                        **token_data,
                    }
                    return self.async_create_entry(title=self._entry_title, data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_GLS_REFRESH_TOKEN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="gls_credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )

    async def async_step_unsupported(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Afiseaza mesaj pentru curierii planificati, dar inca neactivati."""

        if user_input is not None:
            return await self.async_step_service()

        courier_name = COURIER_NAMES.get(self._courier or "", "Curierul selectat")
        return self.async_show_form(
            step_id="unsupported",
            data_schema=vol.Schema({}),
            errors={"base": "unsupported_courier"},
            description_placeholders={"courier": courier_name},
        )

    async def async_step_sameday_auth_method(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Alege metoda de autentificare Sameday."""

        if user_input is not None:
            method = str(user_input.get(FIELD_SAMEDAY_AUTH_METHOD, METHOD_SAMEDAY_PHONE_PASSWORD))
            if method == METHOD_SAMEDAY_PHONE_PASSWORD:
                return await self.async_step_sameday_login()
            if method == METHOD_SAMEDAY_REFRESH_TOKEN:
                return await self.async_step_sameday_helper()
            self._prepare_sameday_authorization()
            return await self.async_step_sameday_oauth()

        schema = vol.Schema(
            {
                vol.Required(
                    FIELD_SAMEDAY_AUTH_METHOD,
                    default=METHOD_SAMEDAY_PHONE_PASSWORD,
                ): vol.In(
                    {
                        METHOD_SAMEDAY_PHONE_PASSWORD: "Telefon si parola Sameday (recomandat)",
                        METHOD_SAMEDAY_REFRESH_TOKEN: "Helper Sameday / bookmarklet (avansat)",
                        METHOD_SAMEDAY_CALLBACK_URL: "URL callback OAuth / metoda avansata",
                    }
                )
            }
        )
        return self.async_show_form(
            step_id="sameday_auth_method",
            data_schema=schema,
            errors={},
            description_placeholders={"helper_url": SAMEDAY_HELPER_URL},
        )

    async def async_step_sameday_login(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Autentificare Sameday automata cu telefon/parola."""

        errors: dict[str, str] = {}

        if user_input is not None:
            phone = str(user_input.get(FIELD_SAMEDAY_PHONE, "")).strip()
            password = str(user_input.get(FIELD_SAMEDAY_PASSWORD, ""))
            if not phone:
                errors[FIELD_SAMEDAY_PHONE] = "required"
            if not password:
                errors[FIELD_SAMEDAY_PASSWORD] = "required"

            if not errors:
                try:
                    token_data = await self._async_login_sameday_with_password(phone, password)
                except ValueError as err:
                    errors["base"] = str(err)
                except Exception:  # pragma: no cover - protectie runtime HA
                    errors["base"] = "cannot_connect"
                else:
                    data = {
                        CONF_ENTRY_TYPE: ENTRY_TYPE_COURIER,
                        CONF_COURIER: COURIER_SAMEDAY,
                        CONF_INSTANCE_NAME: self._entry_title,
                        CONF_ENABLED_COURIERS: [COURIER_SAMEDAY],
                        **token_data,
                    }
                    return self.async_create_entry(title=self._entry_title, data=data)

        schema = vol.Schema(
            {
                vol.Required(FIELD_SAMEDAY_PHONE): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Required(FIELD_SAMEDAY_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="sameday_login",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )

    async def async_step_sameday_helper(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Deschide helperul Sameday prin pas extern Home Assistant."""

        if user_input is not None:
            token = self._pop_pending_helper_token(COURIER_SAMEDAY)
            if token:
                self._default_sameday_refresh_token = token
                result = await self._async_try_create_sameday_entry(token)
                if result is not None:
                    return result
            return self.async_external_step_done(next_step_id="sameday_refresh_token")

        return self.async_external_step(
            step_id="sameday_helper",
            url=self._build_helper_url(SAMEDAY_HELPER_URL, COURIER_SAMEDAY),
        )


    async def async_step_sameday_refresh_token(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configureaza Sameday folosind refresh token extras din portal."""

        errors: dict[str, str] = {}

        if user_input is not None:
            refresh_token = str(user_input.get(CONF_SAMEDAY_REFRESH_TOKEN, "")).strip()
            if not refresh_token:
                errors[CONF_SAMEDAY_REFRESH_TOKEN] = "required"
            else:
                try:
                    token_data = await self._async_validate_sameday_refresh_token(refresh_token)
                except ValueError as err:
                    errors["base"] = str(err)
                except Exception:  # pragma: no cover - protectie runtime HA
                    errors["base"] = "cannot_connect"
                else:
                    data = {
                        CONF_ENTRY_TYPE: ENTRY_TYPE_COURIER,
                        CONF_COURIER: COURIER_SAMEDAY,
                        CONF_INSTANCE_NAME: self._entry_title,
                        CONF_ENABLED_COURIERS: [COURIER_SAMEDAY],
                        **token_data,
                    }
                    return self.async_create_entry(title=self._entry_title, data=data)

        default_token = self._default_sameday_refresh_token or ""
        schema = vol.Schema(
            {
                vol.Required(CONF_SAMEDAY_REFRESH_TOKEN, default=default_token): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                )
            }
        )
        return self.async_show_form(
            step_id="sameday_refresh_token",
            data_schema=schema,
            errors=errors,
            description_placeholders={"helper_url": SAMEDAY_HELPER_URL},
        )

    async def async_step_sameday_oauth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Autentificare Sameday prin fluxul web OAuth/OIDC cu PKCE."""

        errors: dict[str, str] = {}

        if self._sameday_authorization_url is None:
            self._prepare_sameday_authorization()

        if user_input is not None:
            callback_url = str(user_input.get(FIELD_CALLBACK_URL, "")).strip()
            if not callback_url:
                errors[FIELD_CALLBACK_URL] = "required"
            else:
                try:
                    code = self._extract_sameday_code(callback_url)
                    token_data = await self._async_exchange_sameday_code(code)
                except ValueError as err:
                    errors["base"] = str(err)
                except Exception:  # pragma: no cover - protectie runtime HA
                    errors["base"] = "cannot_connect"
                else:
                    data = {
                        CONF_ENTRY_TYPE: ENTRY_TYPE_COURIER,
                        CONF_COURIER: COURIER_SAMEDAY,
                        CONF_INSTANCE_NAME: self._entry_title,
                        CONF_ENABLED_COURIERS: [COURIER_SAMEDAY],
                        **token_data,
                    }
                    return self.async_create_entry(title=self._entry_title, data=data)

        schema = vol.Schema(
            {
                vol.Required(
                    FIELD_AUTHORIZATION_URL,
                    default=self._sameday_authorization_url or "",
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Required(FIELD_CALLBACK_URL): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
            }
        )
        return self.async_show_form(
            step_id="sameday_oauth",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "authorization_url": self._sameday_authorization_url or "",
            },
        )

    def _build_helper_url(self, helper_path: str, courier: str | None = None) -> str:
        """Construieste URL-ul complet pentru helperul local din pasul extern."""

        params = {"flow_id": self.flow_id}
        if courier:
            params["courier"] = courier
        query = urlencode(params)
        path = f"{helper_path}?{query}"
        try:
            base_url = get_url(self.hass, allow_internal=True, allow_external=True, allow_cloud=True)
        except Exception:
            # Fallback pentru instalari fara URL intern/extern configurat.
            return path
        return f"{base_url.rstrip('/')}{path}"

    def _pop_pending_helper_token(self, expected_courier: str) -> str | None:
        """Ia tokenul trimis de bookmarklet pentru flow-ul curent."""

        pending = self.hass.data.setdefault(DOMAIN, {}).setdefault("pending_helper_tokens", {})
        item = pending.pop(self.flow_id, None)
        if not isinstance(item, dict):
            return None
        courier = str(item.get("courier") or "")
        token = str(item.get("refresh_token") or "").strip()
        if courier and courier != expected_courier:
            return None
        return token or None

    async def _async_try_create_sameday_entry(self, refresh_token: str) -> FlowResult | None:
        """Valideaza refresh tokenul Sameday si creeaza intrarea daca este valid."""

        try:
            token_data = await self._async_validate_sameday_refresh_token(refresh_token)
        except Exception:
            return None
        data = {
            CONF_ENTRY_TYPE: ENTRY_TYPE_COURIER,
            CONF_COURIER: COURIER_SAMEDAY,
            CONF_INSTANCE_NAME: self._entry_title,
            CONF_ENABLED_COURIERS: [COURIER_SAMEDAY],
            **token_data,
        }
        return self.async_create_entry(title=self._entry_title, data=data)

    async def _async_try_create_cargus_entry(self, phone: str, refresh_token: str) -> FlowResult | None:
        """Valideaza refresh tokenul Cargus si creeaza intrarea daca este valid."""

        try:
            token_data = await self._async_validate_cargus_refresh_token(phone, refresh_token)
        except Exception:
            return None
        data = {
            CONF_ENTRY_TYPE: ENTRY_TYPE_COURIER,
            CONF_COURIER: COURIER_CARGUS,
            CONF_INSTANCE_NAME: self._entry_title,
            CONF_ENABLED_COURIERS: [COURIER_CARGUS],
            CONF_CARGUS_PHONE: phone,
            CONF_CARGUS_REFRESH_TOKEN: token_data[CONF_CARGUS_REFRESH_TOKEN],
            CONF_CARGUS_ACCESS_TOKEN: token_data[CONF_CARGUS_ACCESS_TOKEN],
            CONF_CARGUS_TOKEN_EXPIRES_AT: token_data[CONF_CARGUS_TOKEN_EXPIRES_AT],
        }
        return self.async_create_entry(title=self._entry_title, data=data)

    async def _async_create_cargus_from_refresh_token(self, refresh_token: str) -> FlowResult:
        """Creeaza Cargus automat daca avem telefon si token din helper."""

        phone = _normalize_phone(self._cargus_phone or "")
        if phone:
            result = await self._async_try_create_cargus_entry(phone, refresh_token)
            if result is not None:
                return result
        self._default_cargus_refresh_token = refresh_token
        return self.async_external_step_done(next_step_id="cargus_credentials")


    def _admin_entry_exists(self) -> bool:
        """Verifica daca intrarea de administrare exista deja."""

        return any(
            entry.unique_id == ADMIN_UNIQUE_ID
            or entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_ADMIN
            for entry in self._async_current_entries()
        )

    def _prepare_gls_authorization(self) -> None:
        """Genereaza URL-ul de autentificare GLS cu PKCE, cat mai fidel MSAL Android."""

        self._gls_code_verifier = _gls_make_code_verifier()
        code_challenge = _gls_make_code_challenge(self._gls_code_verifier)
        self._gls_state = _gls_make_state()
        self._gls_nonce = None
        client_request_id = str(uuid.uuid4())

        query = urlencode(
            {
                "prompt": "login",
                "client-request-id": client_request_id,
                "x-client-CPU": "x86_64",
                "x-client-DM": "SM-S9260",
                "x-client-MN": "samsung",
                "x-client-OS": "28",
                "x-client-ReleaseOS": "9",
                "x-client-SKU": "MSAL.Android",
                "x-client-Ver": "8.2.1",
                "instance_aware": "false",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "x-client-WPAvailable": "false",
                "client_id": GLS_CLIENT_ID,
                "redirect_uri": GLS_REDIRECT_URI,
                "response_type": "code",
                "scope": GLS_AUTH_SCOPE,
                "state": self._gls_state,
                "flow_type": "signIn",
                "ui_locales": "en",
                "region": "RO",
            },
            quote_via=quote,
        )
        self._gls_authorization_url = f"{GLS_AUTHORIZE_URL}?{query}"

    def _extract_gls_code(self, callback_url: str) -> str:
        """Extrage codul OAuth GLS din URL-ul final cu schema msauth."""

        parsed = urlparse(callback_url)
        query = parse_qs(parsed.query)
        fragment = parse_qs(parsed.fragment)
        code = query.get("code", fragment.get("code", [None]))[0]
        state = query.get("state", fragment.get("state", [None]))[0]
        error = query.get("error", fragment.get("error", [None]))[0]

        if error:
            raise ValueError("invalid_auth_gls")
        if not code:
            raise ValueError("missing_code")
        if self._gls_state and state and state != self._gls_state:
            raise ValueError("invalid_state")
        return code

    def _prepare_sameday_authorization(self) -> None:
        """Genereaza URL-ul de autentificare Sameday cu PKCE."""

        self._sameday_code_verifier = secrets.token_hex(48)
        digest = hashlib.sha256(self._sameday_code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        self._sameday_state = secrets.token_hex(16)

        query = urlencode(
            {
                "client_id": SAMEDAY_WEB_CLIENT_ID,
                "redirect_uri": SAMEDAY_WEB_REDIRECT_URI,
                "response_type": "code",
                "scope": SAMEDAY_WEB_SCOPES,
                "state": self._sameday_state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "response_mode": "query",
                "ui_locales": "ro-RO",
            }
        )
        self._sameday_authorization_url = f"{SAMEDAY_IDENTITY_BASE}/connect/authorize?{query}"

    def _extract_sameday_code(self, callback_url: str) -> str:
        """Extrage codul OAuth din URL-ul final Sameday si valideaza state-ul."""

        parsed = urlparse(callback_url)
        query = parse_qs(parsed.query)
        code = query.get("code", [None])[0]
        state = query.get("state", [None])[0]
        error = query.get("error", [None])[0]

        if error:
            raise ValueError("invalid_auth")
        if not code:
            raise ValueError("missing_code")
        if self._sameday_state and state != self._sameday_state:
            raise ValueError("invalid_state")
        return code


    async def _async_validate_fan_login(self, username: str, password: str, api_key: str) -> None:
        """Valideaza datele FAN Courier fara sa salveze tokenurile in config entry."""

        session = async_get_clientsession(self.hass)
        headers = {
            "x-api-key": api_key,
            "accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": FAN_USER_AGENT,
        }
        async with session.post(
            f"{FAN_API_BASE}/mobile/v2/login",
            headers=headers,
            json={"username": username, "password": password},
        ) as response:
            if response.status in (400, 401, 403):
                raise ValueError("invalid_auth_fan")
            if response.status >= 400:
                raise ValueError("cannot_connect_fan")
            payload = await response.json(content_type=None)

        data = payload.get("data") if isinstance(payload, dict) else None
        if payload.get("status") != "success" or not isinstance(data, dict) or not data.get("token"):
            raise ValueError("invalid_auth_fan")

        # Loginul returneaza userId, dar endpointurile AWB folosesc clientId-ul
        # din lista de branch-uri. Validam existenta lui inca din configurare.
        branch_headers = dict(headers)
        branch_headers["authorization"] = f"Bearer {data['token']}"
        async with session.get(
            f"{FAN_API_BASE}/reports/mobile/branches",
            headers=branch_headers,
        ) as response:
            if response.status in (400, 401, 403):
                raise ValueError("invalid_auth_fan")
            if response.status >= 400:
                raise ValueError("cannot_connect_fan")
            branches_payload = await response.json(content_type=None)

        branches = branches_payload.get("data") if isinstance(branches_payload, dict) else None
        if branches_payload.get("status") != "success" or not isinstance(branches, list):
            raise ValueError("cannot_connect_fan")
        if not any(isinstance(branch, dict) and branch.get("id") for branch in branches):
            raise ValueError("cannot_connect_fan")



    async def _async_login_cargus_with_password(self, email_value: str, password: str) -> dict[str, Any]:
        """Autentifica Cargus prin Azure B2C si intoarce tokenurile initiale.

        Fluxul ruleaza in executor folosind logica validata in scriptul local
        cargus_auth_test_v2.py. Parola este folosita doar in acest pas si nu
        este salvata in config entry.
        """

        try:
            return await self.hass.async_add_executor_job(
                _sync_login_cargus_with_password,
                email_value,
                password,
            )
        except ValueError:
            raise
        except Exception as err:  # pragma: no cover - protectie runtime HA
            _LOGGER.warning(
                "[CARGUS AUTH DIAG] step=unexpected error_type=%s",
                type(err).__name__,
            )
            raise ValueError("cannot_connect_cargus") from err

    async def _async_validate_cargus_refresh_token(self, phone: str, refresh_token: str) -> dict[str, Any]:
        """Valideaza refresh tokenul Cargus si returneaza tokenuri initiale."""

        session = async_get_clientsession(self.hass)
        try:
            token_payload = await _async_refresh_cargus_token(session, refresh_token)
        except Exception as err:
            raise ValueError("invalid_auth_cargus") from err

        access_token = str(token_payload.get("access_token", "")).strip()
        new_refresh_token = str(token_payload.get("refresh_token") or refresh_token).strip()
        if not access_token or not new_refresh_token:
            raise ValueError("invalid_auth_cargus")

        headers = _build_headers(phone=phone, token=access_token)
        async with session.get(
            f"{CARGUS_API_BASE}/api/Awbs/AwbList",
            headers=headers,
            params={"shipmentType": 0, "lang": "RO"},
        ) as response:
            if response.status in (401, 403):
                raise ValueError("invalid_auth_cargus")
            if response.status >= 400:
                raise ValueError("cannot_connect_cargus")
            await response.text()

        expires_in = int(token_payload.get("expires_in", 3600))
        return {
            CONF_CARGUS_ACCESS_TOKEN: f"Bearer {access_token}",
            CONF_CARGUS_REFRESH_TOKEN: new_refresh_token,
            CONF_CARGUS_TOKEN_EXPIRES_AT: time.time() + expires_in,
        }

    async def _async_validate_gls_refresh_token(self, refresh_token: str) -> dict[str, Any]:
        """Valideaza refresh tokenul GLS si returneaza tokenuri initiale."""

        session = async_get_clientsession(self.hass)
        try:
            token_payload = await async_refresh_gls_token(session, refresh_token)
        except Exception as err:
            raise ValueError("invalid_auth_gls") from err

        access_token = str(token_payload.get("access_token", "")).strip()
        new_refresh_token = str(token_payload.get("refresh_token") or refresh_token).strip()
        if not access_token or not new_refresh_token:
            raise ValueError("invalid_auth_gls")

        headers = _build_gls_headers(access_token)
        async with session.get(
            f"{GLS_API_BASE}/platform/v2/parcels/recently",
            headers=headers,
        ) as response:
            if response.status in (401, 403):
                raise ValueError("invalid_auth_gls")
            if response.status >= 400:
                raise ValueError("cannot_connect_gls")
            await response.text()

        expires_in = int(token_payload.get("expires_in", 86400))
        return {
            CONF_GLS_ACCESS_TOKEN: access_token,
            CONF_GLS_REFRESH_TOKEN: new_refresh_token,
            CONF_GLS_TOKEN_EXPIRES_AT: time.time() + expires_in,
        }

    async def _async_login_sameday_with_password(self, phone: str, password: str) -> dict[str, Any]:
        """Autentifica Sameday prin telefon/parola si intoarce tokenurile initiale.

        Fluxul ruleaza in executor folosind logica validata in scriptul local
        sameday_auth_test_v2.py. Parola este folosita doar in acest pas si nu
        este salvata in config entry.
        """

        try:
            return await self.hass.async_add_executor_job(
                _sync_login_sameday_with_password,
                phone,
                password,
            )
        except ValueError:
            raise
        except Exception as err:  # pragma: no cover - protectie runtime HA
            _LOGGER.warning(
                "[SAMEDAY AUTH DIAG] step=unexpected error_type=%s",
                type(err).__name__,
            )
            raise ValueError("cannot_connect") from err

    async def _async_validate_sameday_refresh_token(self, refresh_token: str) -> dict[str, Any]:
        """Valideaza refresh tokenul Sameday si returneaza tokenuri initiale."""

        session = async_get_clientsession(self.hass)
        payload = {
            "grant_type": "refresh_token",
            "client_id": SAMEDAY_WEB_CLIENT_ID,
            "refresh_token": refresh_token,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}

        async with session.post(
            f"{SAMEDAY_IDENTITY_BASE}/connect/token",
            data=payload,
            headers=headers,
        ) as response:
            if response.status in (400, 401, 403):
                raise ValueError("invalid_auth")
            if response.status >= 400:
                raise ValueError("cannot_connect")
            token_response = await response.json(content_type=None)

        access_token = str(token_response.get("access_token") or "").strip()
        new_refresh_token = str(token_response.get("refresh_token") or refresh_token).strip()
        if not access_token or not new_refresh_token:
            raise ValueError("invalid_auth")

        api_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "x-platform": "web",
        }
        async with session.get(
            f"{SAMEDAY_API_BASE}/api/awbs?api-version=2.0",
            headers=api_headers,
        ) as response:
            if response.status in (401, 403):
                raise ValueError("invalid_auth")
            if response.status >= 400:
                raise ValueError("cannot_connect")
            await response.text()

        expires_in = int(token_response.get("expires_in", 3600))
        return {
            CONF_SAMEDAY_ACCESS_TOKEN: access_token,
            CONF_SAMEDAY_REFRESH_TOKEN: new_refresh_token,
            CONF_SAMEDAY_ID_TOKEN: token_response.get("id_token"),
            CONF_SAMEDAY_TOKEN_TYPE: token_response.get("token_type", "Bearer"),
            CONF_SAMEDAY_SCOPE: token_response.get("scope"),
            CONF_SAMEDAY_TOKEN_EXPIRES_AT: time.time() + expires_in,
        }


    async def _async_login_gls_with_password(self, email_value: str, password: str) -> dict[str, Any]:
        """Autentifica GLS prin Azure B2C si intoarce tokenurile initiale.

        Fluxul este rulat in executor folosind aceeasi logica validata in
        scriptul local gls_auth_test.py. Parola este folosita doar in acest
        pas si nu este salvata in config entry.
        """

        try:
            return await self.hass.async_add_executor_job(
                _sync_login_gls_with_password,
                email_value,
                password,
            )
        except ValueError:
            raise
        except Exception as err:  # pragma: no cover - protectie runtime HA
            _LOGGER.warning(
                "[GLS AUTH DIAG] step=unexpected error_type=%s",
                type(err).__name__,
            )
            raise ValueError("cannot_connect_gls") from err


    async def _async_exchange_gls_code(self, code: str) -> dict[str, Any]:
        """Schimba authorization code-ul GLS in tokenuri si valideaza API-ul."""

        if not self._gls_code_verifier:
            raise ValueError("invalid_state")
        return await self._async_exchange_gls_code_with_verifier(
            code=code,
            code_verifier=self._gls_code_verifier,
            client_request_id=str(uuid.uuid4()),
        )

    async def _async_exchange_gls_code_with_verifier(
        self,
        *,
        code: str,
        code_verifier: str,
        client_request_id: str,
    ) -> dict[str, Any]:
        """Schimba authorization code-ul GLS in tokenuri folosind verifierul PKCE."""

        session = async_get_clientsession(self.hass)
        payload = {
            "client-request-id": client_request_id,
            "grant_type": "authorization_code",
            "client_id": GLS_CLIENT_ID,
            "client_info": "1",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": GLS_REDIRECT_URI,
            "scope": GLS_AUTH_SCOPE,
            "x-app-name": "com.gls.loyalty.ro",
            "x-app-ver": "1.113.0",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": GLS_DALVIK_UA,
            "x-client-SKU": "MSAL.Android",
            "x-client-OS": "28",
            "x-client-DM": "SM-S9260",
            "x-client-MN": "samsung",
            "x-client-CPU": "x86_64",
            "x-client-Ver": "8.2.1",
            "client-request-id": client_request_id,
            "x-app-name": "com.gls.loyalty.ro",
            "x-app-ver": "1.113.0",
        }

        async with session.post(GLS_TOKEN_URL, data=payload, headers=headers) as response:
            if response.status in (400, 401, 403):
                raise ValueError("invalid_auth_gls")
            if response.status >= 400:
                raise ValueError("cannot_connect_gls")
            token_response = await response.json(content_type=None)

        access_token = str(token_response.get("access_token") or "").strip()
        refresh_token = str(token_response.get("refresh_token") or "").strip()
        if not access_token or not refresh_token:
            raise ValueError("invalid_auth_gls")

        headers_api = _build_gls_headers(access_token)
        async with session.get(f"{GLS_API_BASE}/platform/v2/parcels/recently", headers=headers_api) as response:
            if response.status in (401, 403):
                raise ValueError("invalid_auth_gls")
            if response.status >= 400:
                raise ValueError("cannot_connect_gls")
            await response.text()

        expires_in = int(token_response.get("expires_in", 86400))
        return {
            CONF_GLS_ACCESS_TOKEN: access_token,
            CONF_GLS_REFRESH_TOKEN: refresh_token,
            CONF_GLS_TOKEN_EXPIRES_AT: time.time() + expires_in,
        }

    async def _async_exchange_sameday_code(self, code: str) -> dict[str, Any]:
        """Schimba authorization code-ul Sameday in tokenuri."""

        if not self._sameday_code_verifier:
            raise ValueError("invalid_state")

        session = async_get_clientsession(self.hass)
        payload = {
            "grant_type": "authorization_code",
            "redirect_uri": SAMEDAY_WEB_REDIRECT_URI,
            "code": code,
            "code_verifier": self._sameday_code_verifier,
            "client_id": SAMEDAY_WEB_CLIENT_ID,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}

        async with session.post(
            f"{SAMEDAY_IDENTITY_BASE}/connect/token",
            data=payload,
            headers=headers,
        ) as response:
            if response.status in (400, 401, 403):
                raise ValueError("invalid_auth")
            if response.status >= 400:
                raise RuntimeError(f"Sameday token HTTP {response.status}")
            token_response = await response.json(content_type=None)

        access_token = token_response.get("access_token")
        if not access_token:
            raise ValueError("invalid_auth")

        expires_in = int(token_response.get("expires_in", 3600))
        return {
            CONF_SAMEDAY_ACCESS_TOKEN: access_token,
            CONF_SAMEDAY_REFRESH_TOKEN: token_response.get("refresh_token"),
            CONF_SAMEDAY_ID_TOKEN: token_response.get("id_token"),
            CONF_SAMEDAY_TOKEN_TYPE: token_response.get("token_type", "Bearer"),
            CONF_SAMEDAY_SCOPE: token_response.get("scope"),
            CONF_SAMEDAY_TOKEN_EXPIRES_AT: time.time() + expires_in,
        }




def _mask_sameday_value(value: str, keep: int = 12) -> str:
    """Mascheaza valori sensibile pentru loguri Sameday."""

    if not value:
        return ""
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}...***...{value[-keep:]}"


def _mask_sameday_phone(value: str) -> str:
    """Mascheaza telefonul Sameday pentru loguri."""

    digits = re.sub(r"\D+", "", value or "")
    if len(digits) < 6:
        return "***"
    return f"***{digits[-3:]}"


def _sameday_log_diag(step: str, *, status: int | None = None, detail: str | None = None) -> None:
    """Scrie diagnostic Sameday sigur, fara parole/tokenuri."""

    parts = [f"step={step}"]
    if status is not None:
        parts.append(f"status={status}")
    if detail:
        parts.append(f"detail={detail}")
    _LOGGER.warning("[SAMEDAY AUTH DIAG] %s", " ".join(parts))


def _normalize_sameday_phone(raw: str) -> tuple[str, str]:
    """Normalizeaza telefonul Sameday in FullPhoneNumber si PhoneNumber."""

    digits = re.sub(r"\D+", "", raw or "")
    if digits.startswith("0040"):
        digits = digits[2:]
    if digits.startswith("40"):
        local = digits[2:]
        full = f"+{digits}"
    elif digits.startswith("0"):
        local = digits[1:]
        full = f"+40{local}"
    else:
        local = digits
        full = f"+40{local}"
    return full, local


def _parse_sameday_mobile_login_form(page_text: str, current_url: str) -> tuple[str, dict[str, str]]:
    """Extrage action si inputurile din formularul MobileLogin Sameday."""

    forms = re.findall(r"<form\b[^>]*>.*?</form>", page_text, flags=re.IGNORECASE | re.DOTALL)
    debug_forms: list[str] = []
    for index, form in enumerate(forms):
        names = [
            html.unescape(match.group(1))
            for match in re.finditer(r"\bname=[\"']([^\"']+)[\"']", form, flags=re.IGNORECASE)
        ]
        debug_forms.append(f"form#{index} names={names}")
        if "Password" not in names and not re.search(r"type=[\"']password[\"']", form, flags=re.IGNORECASE):
            continue

        action_match = re.search(r"\baction=[\"']([^\"']*)[\"']", form, flags=re.IGNORECASE)
        action = html.unescape(action_match.group(1)) if action_match else current_url
        action_url = urljoin(current_url, action)

        data: dict[str, str] = {}
        for input_match in re.finditer(r"<(?:input|button)\b[^>]*>", form, flags=re.IGNORECASE | re.DOTALL):
            tag = input_match.group(0)
            name_match = re.search(r"\bname=[\"']([^\"']+)[\"']", tag, flags=re.IGNORECASE)
            if not name_match:
                continue
            name = html.unescape(name_match.group(1))
            value_match = re.search(r"\bvalue=[\"']([^\"']*)[\"']", tag, flags=re.IGNORECASE | re.DOTALL)
            data[name] = html.unescape(value_match.group(1)) if value_match else ""
        return action_url, data

    _sameday_log_diag("parse_form", detail=_mask_sameday_value(" | ".join(debug_forms), keep=80))
    raise ValueError("cannot_connect")


def _sync_login_sameday_with_password(phone: str, password: str) -> dict[str, Any]:
    """Autentifica Sameday sincron, cu requests, in executor HA."""

    try:
        import requests
    except ImportError as err:  # pragma: no cover - requests exista in HA
        _sameday_log_diag("import_requests", detail="requests_missing")
        raise ValueError("cannot_connect") from err

    full_phone, phone_number = _normalize_sameday_phone(phone)
    if not phone_number or len(phone_number) < 8:
        _sameday_log_diag("phone", detail="invalid_phone")
        raise ValueError("invalid_auth")

    session = requests.Session()
    code_verifier = _gls_b64url(secrets.token_bytes(48))
    code_challenge = _gls_make_code_challenge(code_verifier)
    state = uuid.uuid4().hex

    authorize_query = urlencode(
        {
            "client_id": SAMEDAY_WEB_CLIENT_ID,
            "redirect_uri": SAMEDAY_WEB_REDIRECT_URI,
            "response_type": "code",
            "scope": SAMEDAY_WEB_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "response_mode": "query",
            "ui_locales": "ro-RO",
        },
        quote_via=quote,
    )
    authorization_url = f"{SAMEDAY_IDENTITY_BASE}/connect/authorize?{authorize_query}"

    browser_headers = {
        "User-Agent": CARGUS_BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        auth_response = session.get(
            authorization_url,
            headers=browser_headers,
            allow_redirects=True,
            timeout=30,
        )
    except requests.RequestException as err:
        _sameday_log_diag("authorize", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect") from err

    if auth_response.status_code >= 400:
        _sameday_log_diag("authorize", status=auth_response.status_code, detail=f"phone={_mask_sameday_phone(phone)}")
        raise ValueError("cannot_connect")

    if "invalid_request" in auth_response.text.lower():
        _sameday_log_diag("authorize", detail="invalid_request")
        raise ValueError("cannot_connect")

    try:
        action_url, form_data = _parse_sameday_mobile_login_form(auth_response.text, auth_response.url)
    except ValueError:
        _sameday_log_diag(
            "parse_form",
            detail=f"final_url={_mask_sameday_value(auth_response.url, keep=48)} has_password={'password' in auth_response.text.lower()}",
        )
        raise

    post_data = dict(form_data)
    post_data["FullPhoneNumber"] = full_phone
    post_data["PhoneNumber"] = phone_number
    post_data["Password"] = password
    post_data["button"] = "login"
    post_data["RememberLogin"] = "false"

    post_headers = dict(browser_headers)
    post_headers.update(
        {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": SAMEDAY_IDENTITY_BASE,
            "Referer": auth_response.url,
        }
    )

    try:
        login_response = session.post(
            action_url,
            headers=post_headers,
            data=post_data,
            allow_redirects=False,
            timeout=30,
        )
    except requests.RequestException as err:
        _sameday_log_diag("login", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect") from err

    location = login_response.headers.get("location") or login_response.headers.get("Location")
    if login_response.status_code not in (301, 302, 303, 307, 308) or not location:
        detail = f"has_location={bool(location)} has_invalid={'invalid' in login_response.text.lower()}"
        _sameday_log_diag("login", status=login_response.status_code, detail=detail)
        if login_response.status_code in (400, 401, 403) or "invalid" in login_response.text.lower():
            raise ValueError("invalid_auth")
        raise ValueError("cannot_connect")

    code: str | None = None
    current_url = action_url
    for hop in range(1, 11):
        next_url = urljoin(current_url, location)
        code = _extract_gls_code_from_location(next_url)
        if code:
            break
        parsed = urlparse(next_url)
        if parsed.scheme not in ("http", "https"):
            break

        try:
            redirect_response = session.get(
                next_url,
                headers=browser_headers,
                allow_redirects=False,
                timeout=30,
            )
        except requests.RequestException as err:
            _sameday_log_diag("redirect", detail=f"hop={hop} request_exception:{type(err).__name__}")
            raise ValueError("cannot_connect") from err

        location = redirect_response.headers.get("location") or redirect_response.headers.get("Location")
        current_url = next_url
        if not location:
            _sameday_log_diag("redirect", status=redirect_response.status_code, detail=f"hop={hop} missing_location")
            break

    if not code:
        _sameday_log_diag("redirect", detail="missing_code")
        raise ValueError("cannot_connect")

    token_payload = {
        "grant_type": "authorization_code",
        "redirect_uri": SAMEDAY_WEB_REDIRECT_URI,
        "code": code,
        "code_verifier": code_verifier,
        "client_id": SAMEDAY_WEB_CLIENT_ID,
    }
    token_headers = {
        "User-Agent": CARGUS_BROWSER_UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    try:
        token_response = session.post(
            f"{SAMEDAY_IDENTITY_BASE}/connect/token",
            headers=token_headers,
            data=token_payload,
            timeout=30,
        )
    except requests.RequestException as err:
        _sameday_log_diag("token", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect") from err

    if token_response.status_code in (400, 401, 403):
        detail = ""
        try:
            payload = token_response.json()
            detail = str(payload.get("error") or payload.get("error_description") or "")
        except ValueError:
            detail = "non_json_response"
        _sameday_log_diag("token", status=token_response.status_code, detail=_mask_sameday_value(detail, keep=32))
        raise ValueError("invalid_auth")

    if token_response.status_code >= 400:
        _sameday_log_diag("token", status=token_response.status_code)
        raise ValueError("cannot_connect")

    try:
        token_json = token_response.json()
    except ValueError as err:
        _sameday_log_diag("token", detail="non_json_success")
        raise ValueError("cannot_connect") from err

    access_token = str(token_json.get("access_token") or "").strip()
    refresh_token = str(token_json.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        _sameday_log_diag("token_payload", detail=f"has_access={bool(access_token)} has_refresh={bool(refresh_token)}")
        raise ValueError("invalid_auth")

    api_headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "x-platform": "web",
    }
    try:
        api_response = session.get(
            f"{SAMEDAY_API_BASE}/api/awbs?api-version=2.0",
            headers=api_headers,
            timeout=30,
        )
    except requests.RequestException as err:
        _sameday_log_diag("api_validate", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect") from err

    if api_response.status_code in (401, 403):
        _sameday_log_diag("api_validate", status=api_response.status_code)
        raise ValueError("invalid_auth")
    if api_response.status_code >= 400:
        _sameday_log_diag("api_validate", status=api_response.status_code)
        raise ValueError("cannot_connect")

    expires_in = int(token_json.get("expires_in", 3600))
    _LOGGER.warning(
        "[SAMEDAY AUTH DIAG] step=success phone=%s expires_in=%s",
        _mask_sameday_phone(phone),
        expires_in,
    )
    return {
        CONF_SAMEDAY_ACCESS_TOKEN: access_token,
        CONF_SAMEDAY_REFRESH_TOKEN: refresh_token,
        CONF_SAMEDAY_ID_TOKEN: token_json.get("id_token"),
        CONF_SAMEDAY_TOKEN_TYPE: token_json.get("token_type", "Bearer"),
        CONF_SAMEDAY_SCOPE: token_json.get("scope"),
        CONF_SAMEDAY_TOKEN_EXPIRES_AT: time.time() + expires_in,
    }


def _mask_cargus_value(value: str, keep: int = 12) -> str:
    """Mascheaza valori sensibile pentru loguri Cargus."""

    if not value:
        return ""
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}...***...{value[-keep:]}"


def _mask_cargus_email(value: str) -> str:
    """Mascheaza emailul pentru loguri Cargus."""

    if "@" not in value:
        return "***"
    local, domain = value.split("@", 1)
    local_masked = local[:2] + "***" if len(local) > 2 else "***"
    return f"{local_masked}@{domain}"


def _cargus_log_diag(step: str, *, status: int | None = None, detail: str | None = None) -> None:
    """Scrie diagnostic Cargus sigur, fara parole/tokenuri."""

    parts = [f"step={step}"]
    if status is not None:
        parts.append(f"status={status}")
    if detail:
        parts.append(f"detail={detail}")
    _LOGGER.warning("[CARGUS AUTH DIAG] %s", " ".join(parts))


def _sync_login_cargus_with_password(email_value: str, password: str) -> dict[str, Any]:
    """Autentifica Cargus sincron, cu requests, in executor HA."""

    try:
        import requests
    except ImportError as err:  # pragma: no cover - requests exista in HA
        _cargus_log_diag("import_requests", detail="requests_missing")
        raise ValueError("cannot_connect_cargus") from err

    safe_email = _mask_cargus_email(email_value)
    session = requests.Session()
    code_verifier = _gls_make_code_verifier()
    code_challenge = _gls_make_code_challenge(code_verifier)
    state = _gls_make_state()
    nonce = _gls_b64url(secrets.token_bytes(16))
    client_request_id = str(uuid.uuid4())

    authorization_url = _build_cargus_authorization_url(
        code_challenge=code_challenge,
        state=state,
        nonce=nonce,
        client_request_id=client_request_id,
    )

    browser_headers = {
        "User-Agent": CARGUS_BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        response = session.get(
            authorization_url,
            headers=browser_headers,
            allow_redirects=True,
            timeout=30,
        )
    except requests.RequestException as err:
        _cargus_log_diag("authorize", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect_cargus") from err

    if response.status_code >= 400:
        _cargus_log_diag("authorize", status=response.status_code, detail=f"email={safe_email}")
        raise ValueError("cannot_connect_cargus")

    final_authorize_url = response.url
    values = _extract_cargus_b2c_values(response.text, final_authorize_url, session)
    tx = values.get("tx")
    csrf = values.get("csrf")
    if not tx or not csrf:
        _cargus_log_diag(
            "extract_tx_csrf",
            detail=f"has_tx={bool(tx)} has_csrf={bool(csrf)} final_url_has_tx={'tx=' in final_authorize_url}",
        )
        raise ValueError("cannot_connect_cargus")

    post_headers = {
        "User-Agent": CARGUS_BROWSER_UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "x-csrf-token": csrf,
        "Referer": final_authorize_url,
        "Origin": CARGUS_HOST,
        "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    post_body = {
        "signInName": email_value,
        "password": password,
        "request_type": "RESPONSE",
    }
    self_asserted_url = (
        f"{CARGUS_HOST}/{CARGUS_TENANT}/{CARGUS_POLICY}/SelfAsserted"
        f"?tx={quote(tx, safe='=')}&p={CARGUS_POLICY}"
    )

    try:
        post_response = session.post(
            self_asserted_url,
            headers=post_headers,
            data=post_body,
            allow_redirects=False,
            timeout=30,
        )
    except requests.RequestException as err:
        _cargus_log_diag("selfasserted", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect_cargus") from err

    if post_response.status_code >= 400:
        _cargus_log_diag("selfasserted", status=post_response.status_code)
        if post_response.status_code in (400, 401, 403):
            raise ValueError("invalid_auth_cargus")
        raise ValueError("cannot_connect_cargus")

    try:
        post_payload = post_response.json()
    except ValueError:
        post_payload = None

    if isinstance(post_payload, dict):
        status_value = str(post_payload.get("status") or "").lower()
        if status_value and status_value not in ("200", "success", "ok"):
            detail = str(post_payload.get("message") or post_payload.get("error") or status_value)
            _cargus_log_diag("selfasserted_payload", detail=_mask_cargus_value(detail, keep=24))
            raise ValueError("invalid_auth_cargus")

    confirmed_url = (
        f"{CARGUS_HOST}/{CARGUS_TENANT}/{CARGUS_POLICY}/api/CombinedSigninAndSignup/confirmed"
        f"?rememberMe=false&csrf_token={quote(csrf, safe='')}"
        f"&tx={quote(tx, safe='=')}&p={CARGUS_POLICY}"
    )
    confirmed_headers = {
        "User-Agent": CARGUS_BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": final_authorize_url,
        "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        confirmed_response = session.get(
            confirmed_url,
            headers=confirmed_headers,
            allow_redirects=False,
            timeout=30,
        )
    except requests.RequestException as err:
        _cargus_log_diag("confirmed", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect_cargus") from err

    if confirmed_response.status_code not in (301, 302, 303, 307, 308):
        _cargus_log_diag(
            "confirmed",
            status=confirmed_response.status_code,
            detail=f"has_location={bool(confirmed_response.headers.get('location'))}",
        )
        if confirmed_response.status_code in (400, 401, 403):
            raise ValueError("invalid_auth_cargus")
        raise ValueError("cannot_connect_cargus")

    location = confirmed_response.headers.get("location", "")
    if not location:
        _cargus_log_diag("confirmed", status=confirmed_response.status_code, detail="missing_location")
        raise ValueError("cannot_connect_cargus")

    code = _extract_gls_code_from_location(location)
    if not code:
        _cargus_log_diag("confirmed", status=confirmed_response.status_code, detail="missing_code")
        raise ValueError("cannot_connect_cargus")

    location_state = _extract_gls_state_from_location(location)
    if location_state and location_state != state:
        _cargus_log_diag("confirmed", detail="invalid_state")
        raise ValueError("invalid_state")

    token_headers = {
        "User-Agent": CARGUS_BROWSER_UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    token_payload = {
        "client_id": CARGUS_CLIENT_ID,
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": CARGUS_REDIRECT_URI,
        "scope": CARGUS_SCOPE,
    }

    try:
        token_response = session.post(
            CARGUS_TOKEN_URL,
            headers=token_headers,
            data=token_payload,
            timeout=30,
        )
    except requests.RequestException as err:
        _cargus_log_diag("token", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect_cargus") from err

    if token_response.status_code in (400, 401, 403):
        detail = ""
        try:
            payload = token_response.json()
            detail = str(payload.get("error") or payload.get("error_description") or "")
        except ValueError:
            detail = "non_json_response"
        _cargus_log_diag("token", status=token_response.status_code, detail=_mask_cargus_value(detail, keep=32))
        raise ValueError("invalid_auth_cargus")

    if token_response.status_code >= 400:
        _cargus_log_diag("token", status=token_response.status_code)
        raise ValueError("cannot_connect_cargus")

    try:
        token_json = token_response.json()
    except ValueError as err:
        _cargus_log_diag("token", detail="non_json_success")
        raise ValueError("cannot_connect_cargus") from err

    access_token = str(token_json.get("access_token") or "").strip()
    refresh_token = str(token_json.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        _cargus_log_diag(
            "token_payload",
            detail=f"has_access={bool(access_token)} has_refresh={bool(refresh_token)}",
        )
        raise ValueError("invalid_auth_cargus")

    expires_in = int(token_json.get("expires_in", 3600))
    _LOGGER.warning("[CARGUS AUTH DIAG] step=success email=%s expires_in=%s", safe_email, expires_in)
    return {
        CONF_CARGUS_ACCESS_TOKEN: f"Bearer {access_token}",
        CONF_CARGUS_REFRESH_TOKEN: refresh_token,
        CONF_CARGUS_TOKEN_EXPIRES_AT: time.time() + expires_in,
    }


def _build_cargus_authorization_url(
    *,
    code_challenge: str,
    state: str,
    nonce: str,
    client_request_id: str,
) -> str:
    """Construieste URL-ul authorize Cargus cu PKCE."""

    query = urlencode(
        {
            "client_id": CARGUS_CLIENT_ID,
            "redirect_uri": CARGUS_REDIRECT_URI,
            "response_type": "code",
            "scope": CARGUS_SCOPE,
            "state": state,
            "nonce": nonce,
            "prompt": "login",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "client-request-id": client_request_id,
            "response_mode": "query",
        },
        quote_via=quote,
    )
    return f"{CARGUS_AUTHORIZE_URL}?{query}"


def _extract_cargus_b2c_values(page_text: str, current_url: str, session: Any) -> dict[str, str]:
    """Extrage tx si csrf din pagina Azure B2C Cargus."""

    decoded = html.unescape(page_text).replace("\u0026", "&").replace("\/", "/")
    values: dict[str, str] = {}

    tx = _find_first(
        [
            r'["\']transId["\']\s*:\s*["\']([^"\']+)["\']',
            r'["\']tx["\']\s*:\s*["\']([^"\']+)["\']',
            r'tx=(StateProperties=[^"&\'<>\s]+)',
            r'SelfAsserted\?tx=(StateProperties=[^"&\'<>\s]+)',
        ],
        decoded,
    )
    csrf = _find_first(
        [
            r'["\']csrf["\']\s*:\s*["\']([^"\']+)["\']',
            r'["\']csrf_token["\']\s*:\s*["\']([^"\']+)["\']',
            r'["\']csrfToken["\']\s*:\s*["\']([^"\']+)["\']',
            r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']',
            r'csrf_token=([^"&\'<>\s]+)',
        ],
        decoded,
    )

    if not tx:
        parsed = urlparse(current_url)
        query = parse_qs(parsed.query)
        tx_values = query.get("tx")
        if tx_values:
            tx = tx_values[0]

    if not csrf:
        try:
            csrf = session.cookies.get("x-ms-cpim-csrf")
        except Exception:
            csrf = None

    if tx:
        values["tx"] = tx
    if csrf:
        values["csrf"] = csrf
    return values


def _mask_gls_value(value: str, keep: int = 12) -> str:
    """Mascheaza valori sensibile pentru loguri."""

    if not value:
        return ""
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}...***...{value[-keep:]}"


def _mask_gls_email(value: str) -> str:
    """Mascheaza emailul pentru loguri."""

    if "@" not in value:
        return "***"
    local, domain = value.split("@", 1)
    local_masked = local[:2] + "***" if len(local) > 2 else "***"
    return f"{local_masked}@{domain}"


def _gls_log_diag(step: str, *, status: int | None = None, detail: str | None = None) -> None:
    """Scrie diagnostic GLS sigur, fara parole/tokenuri."""

    parts = [f"step={step}"]
    if status is not None:
        parts.append(f"status={status}")
    if detail:
        parts.append(f"detail={detail}")
    _LOGGER.warning("[GLS AUTH DIAG] %s", " ".join(parts))


def _sync_login_gls_with_password(email_value: str, password: str) -> dict[str, Any]:
    """Autentifica GLS sincron, cu requests, in executor HA."""

    try:
        import requests
    except ImportError as err:  # pragma: no cover - requests exista in HA
        _gls_log_diag("import_requests", detail="requests_missing")
        raise ValueError("cannot_connect_gls") from err

    safe_email = _mask_gls_email(email_value)
    session = requests.Session()
    code_verifier = _gls_make_code_verifier()
    code_challenge = _gls_make_code_challenge(code_verifier)
    state = _gls_make_state()
    client_request_id = str(uuid.uuid4())
    authorization_url = _build_gls_authorization_url(
        code_challenge=code_challenge,
        state=state,
        client_request_id=client_request_id,
    )

    browser_headers = {
        "User-Agent": GLS_BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        response = session.get(
            authorization_url,
            headers=browser_headers,
            allow_redirects=True,
            timeout=30,
        )
    except requests.RequestException as err:
        _gls_log_diag("authorize", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect_gls") from err

    if response.status_code >= 400:
        _gls_log_diag("authorize", status=response.status_code, detail=f"email={safe_email}")
        raise ValueError("cannot_connect_gls")

    final_authorize_url = response.url
    values = _extract_gls_b2c_values(response.text, final_authorize_url)
    csrf_cookie = session.cookies.get("x-ms-cpim-csrf")
    if "csrf" not in values and csrf_cookie:
        values["csrf"] = csrf_cookie

    tx = values.get("tx")
    csrf = values.get("csrf")
    if not tx or not csrf:
        _gls_log_diag(
            "extract_tx_csrf",
            detail=f"has_tx={bool(tx)} has_csrf={bool(csrf)} final_url_has_tx={'tx=' in final_authorize_url}",
        )
        raise ValueError("cannot_connect_gls")

    post_headers = {
        "User-Agent": GLS_BROWSER_UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "x-csrf-token": csrf,
        "Referer": final_authorize_url,
        "Origin": "https://login.gls-group.net",
        "Accept-Language": "en-US,en;q=0.9",
    }
    post_body = {
        "signInName": email_value,
        "password": password,
        "request_type": "RESPONSE",
    }
    self_asserted_urls = (
        f"https://login.gls-group.net/glsgroup.onmicrosoft.com/{GLS_POLICY_LOWER}/SelfAsserted"
        f"?tx={quote(tx, safe='=')}&p={GLS_POLICY_LOWER}",
        f"https://login.gls-group.net/glsgroup.onmicrosoft.com/{GLS_POLICY_LOWER}/api/SelfAsserted"
        f"?tx={quote(tx, safe='=')}&p={GLS_POLICY_LOWER}",
    )

    post_response = None
    post_payload: Any = None
    for index, url in enumerate(self_asserted_urls, start=1):
        try:
            current_response = session.post(
                url,
                headers=post_headers,
                data=post_body,
                allow_redirects=False,
                timeout=30,
            )
        except requests.RequestException as err:
            _gls_log_diag("selfasserted", detail=f"candidate={index} request_exception:{type(err).__name__}")
            continue

        post_response = current_response
        if current_response.status_code < 400:
            try:
                post_payload = current_response.json()
            except ValueError:
                post_payload = None
            break

    if post_response is None:
        _gls_log_diag("selfasserted", detail="no_response")
        raise ValueError("cannot_connect_gls")

    if post_response.status_code in (400, 401, 403):
        detail = ""
        try:
            payload = post_response.json()
            message = str(payload.get("message") or payload.get("error_description") or payload.get("error") or "")
            detail = f"message={_mask_gls_value(message, keep=24)}"
        except ValueError:
            detail = "non_json_response"
        _gls_log_diag("selfasserted", status=post_response.status_code, detail=detail)
        raise ValueError("invalid_auth_gls")

    if post_response.status_code >= 400:
        _gls_log_diag("selfasserted", status=post_response.status_code)
        raise ValueError("cannot_connect_gls")

    if isinstance(post_payload, dict):
        status_value = str(post_payload.get("status") or "").lower()
        if status_value and status_value not in ("200", "success", "ok"):
            detail = str(post_payload.get("message") or post_payload.get("error") or status_value)
            _gls_log_diag("selfasserted_payload", detail=_mask_gls_value(detail, keep=24))
            raise ValueError("invalid_auth_gls")

    confirmed_url = (
        f"https://login.gls-group.net/glsgroup.onmicrosoft.com/{GLS_POLICY_LOWER}/api/CombinedSigninAndSignup/confirmed"
        f"?rememberMe=false&csrf_token={quote(csrf, safe='')}"
        f"&tx={quote(tx, safe='=')}&p={GLS_POLICY_LOWER}"
    )
    confirmed_headers = {
        "User-Agent": GLS_BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": final_authorize_url,
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        confirmed_response = session.get(
            confirmed_url,
            headers=confirmed_headers,
            allow_redirects=False,
            timeout=30,
        )
    except requests.RequestException as err:
        _gls_log_diag("confirmed", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect_gls") from err

    if confirmed_response.status_code not in (301, 302, 303, 307, 308):
        _gls_log_diag(
            "confirmed",
            status=confirmed_response.status_code,
            detail=f"has_location={bool(confirmed_response.headers.get('location'))}",
        )
        if confirmed_response.status_code in (400, 401, 403):
            raise ValueError("invalid_auth_gls")
        raise ValueError("cannot_connect_gls")

    location = confirmed_response.headers.get("location", "")
    if not location:
        _gls_log_diag("confirmed", status=confirmed_response.status_code, detail="missing_location")
        raise ValueError("cannot_connect_gls")

    code = _extract_gls_code_from_location(location)
    if not code:
        _gls_log_diag("confirmed", status=confirmed_response.status_code, detail="missing_code")
        raise ValueError("cannot_connect_gls")

    location_state = _extract_gls_state_from_location(location)
    if location_state and location_state != state:
        _gls_log_diag("confirmed", detail="invalid_state")
        raise ValueError("invalid_state")

    token_headers = {
        "User-Agent": GLS_DALVIK_UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "x-client-SKU": "MSAL.Android",
        "x-client-OS": "28",
        "x-client-DM": "SM-S9260",
        "x-client-MN": "samsung",
        "x-client-CPU": "x86_64",
        "x-client-Ver": "8.2.1",
        "client-request-id": client_request_id,
        "x-app-name": "com.gls.loyalty.ro",
        "x-app-ver": "1.113.0",
    }

    token_payload = {
        "client-request-id": client_request_id,
        "client_id": GLS_CLIENT_ID,
        "client_info": "1",
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": GLS_REDIRECT_URI,
        "scope": GLS_AUTH_SCOPE,
        "x-app-name": "com.gls.loyalty.ro",
        "x-app-ver": "1.113.0",
    }

    try:
        token_response = session.post(
            GLS_TOKEN_URL,
            headers=token_headers,
            data=token_payload,
            timeout=30,
        )
    except requests.RequestException as err:
        _gls_log_diag("token", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect_gls") from err

    if token_response.status_code in (400, 401, 403):
        detail = ""
        try:
            payload = token_response.json()
            detail = str(payload.get("error") or payload.get("error_description") or "")
        except ValueError:
            detail = "non_json_response"
        _gls_log_diag("token", status=token_response.status_code, detail=_mask_gls_value(detail, keep=32))
        raise ValueError("invalid_auth_gls")

    if token_response.status_code >= 400:
        _gls_log_diag("token", status=token_response.status_code)
        raise ValueError("cannot_connect_gls")

    try:
        token_json = token_response.json()
    except ValueError as err:
        _gls_log_diag("token", detail="non_json_success")
        raise ValueError("cannot_connect_gls") from err

    access_token = str(token_json.get("access_token") or "").strip()
    refresh_token = str(token_json.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        _gls_log_diag(
            "token_payload",
            detail=f"has_access={bool(access_token)} has_refresh={bool(refresh_token)}",
        )
        raise ValueError("invalid_auth_gls")

    headers_api = _build_gls_headers(access_token)
    try:
        api_response = session.get(
            f"{GLS_API_BASE}/platform/v2/parcels/recently",
            headers=headers_api,
            timeout=30,
        )
    except requests.RequestException as err:
        _gls_log_diag("api_validate", detail=f"request_exception:{type(err).__name__}")
        raise ValueError("cannot_connect_gls") from err

    if api_response.status_code in (401, 403):
        _gls_log_diag("api_validate", status=api_response.status_code)
        raise ValueError("invalid_auth_gls")
    if api_response.status_code >= 400:
        _gls_log_diag("api_validate", status=api_response.status_code)
        raise ValueError("cannot_connect_gls")

    expires_in = int(token_json.get("expires_in", 86400))
    _LOGGER.warning("[GLS AUTH DIAG] step=success email=%s expires_in=%s", safe_email, expires_in)
    return {
        CONF_GLS_ACCESS_TOKEN: access_token,
        CONF_GLS_REFRESH_TOKEN: refresh_token,
        CONF_GLS_TOKEN_EXPIRES_AT: time.time() + expires_in,
    }


def _gls_b64url(data: bytes) -> str:
    """Encodeaza base64url fara padding."""

    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _gls_make_code_verifier() -> str:
    """Genereaza PKCE code_verifier compatibil MSAL."""

    return _gls_b64url(secrets.token_bytes(32))


def _gls_make_code_challenge(verifier: str) -> str:
    """Genereaza PKCE code_challenge."""

    return _gls_b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _gls_make_state() -> str:
    """Genereaza state pentru fluxul GLS."""

    raw = f"{uuid.uuid4()}-{uuid.uuid4()}".encode("utf-8")
    return _gls_b64url(raw)


def _build_gls_authorization_url(*, code_challenge: str, state: str, client_request_id: str) -> str:
    """Construieste URL-ul authorize GLS exact in stilul MSAL Android."""

    query = urlencode(
        {
            "prompt": "login",
            "client-request-id": client_request_id,
            "x-client-CPU": "x86_64",
            "x-client-DM": "SM-S9260",
            "x-client-MN": "samsung",
            "x-client-OS": "28",
            "x-client-ReleaseOS": "9",
            "x-client-SKU": "MSAL.Android",
            "x-client-Ver": "8.2.1",
            "instance_aware": "false",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "x-client-WPAvailable": "false",
            "client_id": GLS_CLIENT_ID,
            "redirect_uri": GLS_REDIRECT_URI,
            "response_type": "code",
            "scope": GLS_AUTH_SCOPE,
            "state": state,
            "flow_type": "signIn",
            "ui_locales": "en",
            "region": "RO",
        },
        quote_via=quote,
    )
    return f"{GLS_AUTHORIZE_URL}?{query}"


def _extract_gls_b2c_values(page_text: str, current_url: str) -> dict[str, str]:
    """Extrage tx si csrf din pagina Azure B2C GLS."""

    decoded = html.unescape(page_text).replace("\\u0026", "&").replace("\\/", "/")
    values: dict[str, str] = {}

    tx = _find_first(
        [
            r'["\\\']transId["\\\']\s*:\s*["\\\']([^"\\\']+)["\\\']',
            r'["\\\']tx["\\\']\s*:\s*["\\\']([^"\\\']+)["\\\']',
            r'tx=(StateProperties=[^"&\\\'<>\\s]+)',
            r'SelfAsserted\?tx=(StateProperties=[^"&\\\'<>\\s]+)',
        ],
        decoded,
    )
    csrf = _find_first(
        [
            r'["\\\']csrf["\\\']\s*:\s*["\\\']([^"\\\']+)["\\\']',
            r'["\\\']csrf_token["\\\']\s*:\s*["\\\']([^"\\\']+)["\\\']',
            r'["\\\']csrfToken["\\\']\s*:\s*["\\\']([^"\\\']+)["\\\']',
            r'name=["\\\']csrf_token["\\\']\s+value=["\\\']([^"\\\']+)["\\\']',
            r'csrf_token=([^"&\\\'<>\\s]+)',
        ],
        decoded,
    )

    if not tx:
        parsed = urlparse(current_url)
        query = parse_qs(parsed.query)
        tx_values = query.get("tx")
        if tx_values:
            tx = tx_values[0]

    if tx:
        values["tx"] = tx
    if csrf:
        values["csrf"] = csrf
    return values


def _find_first(patterns: list[str], text: str) -> str | None:
    """Returneaza primul grup gasit pentru o lista de regex-uri."""

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            value = html.unescape(match.group(1))
            return value.replace("\\u0026", "&").replace("\\/", "/")
    return None


def _extract_gls_code_from_location(location: str) -> str | None:
    """Extrage code din Location msauth://."""

    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    values = query.get("code")
    return values[0] if values else None


def _extract_gls_state_from_location(location: str) -> str | None:
    """Extrage state din Location msauth://."""

    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    values = query.get("state")
    return values[0] if values else None
