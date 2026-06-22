"""Constante pentru integrarea Curieri Romania."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "curieri_romania"
NAME: Final = "Curieri Romania"
VERSION: Final = "1.0.1"

PLATFORMS: Final = ["sensor", "button", "text"]

DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=30)
MIN_SCAN_INTERVAL: Final = timedelta(minutes=10)

SIGNAL_LICENSE_UPDATED: Final = f"{DOMAIN}_license_updated"
SIGNAL_NOTIFICATION_SETTINGS_UPDATED: Final = f"{DOMAIN}_notification_settings_updated"

CONF_LICENSE_KEY: Final = "cheie_licenta"
CONF_LICENSE_USER: Final = "utilizator"
DATE_VERIFICARE_LICENTA: Final = "date_verificare_licenta"
URL_API_LICENTA: Final = "https://license-api.marius-onitiu.workers.dev"
DEFAULT_LICENSE_GRACE_DAYS: Final = 7

LICENSE_STATUS_ACTIVE: Final = "active"
LICENSE_STATUS_TRIAL: Final = "trial"
LICENSE_STATUS_EXPIRED: Final = "expired"
LICENSE_STATUS_INVALID: Final = "invalid"
LICENSE_STATUS_REVOKED: Final = "revoked"
LICENSE_STATUS_INVALID_PRODUCT: Final = "invalid_product"
LICENSE_STATUS_ACTIVATION_LIMIT: Final = "activation_limit"
LICENSE_STATUS_UNKNOWN: Final = "unknown"

SERVICE_REFRESH_LICENSE_STATUS: Final = "refresh_license_status"
SERVICE_SEND_TEST_NOTIFICATION: Final = "send_test_notification"
SERVICE_SIMULATE_NEW_PARCEL_NOTIFICATION: Final = "simulate_new_parcel_notification"
SERVICE_SIMULATE_STATUS_CHANGE_NOTIFICATION: Final = "simulate_status_change_notification"
SERVICE_RESET_TEST_NOTIFICATION_HISTORY: Final = "reset_test_notification_history"
SERVICE_UPDATE_NOTIFICATION_SETTINGS: Final = "update_notification_settings"

STORAGE_KEY_LICENSE: Final = f"{DOMAIN}_licenta"
STORAGE_VERSION_LICENSE: Final = 1
STORAGE_KEY_INSTALLATION: Final = f"{DOMAIN}_installation"
STORAGE_VERSION_INSTALLATION: Final = 1
STORAGE_KEY_NOTIFICATIONS: Final = f"{DOMAIN}_notificari"
STORAGE_VERSION_NOTIFICATIONS: Final = 1
STORAGE_KEY_NOTIFICATION_SETTINGS: Final = f"{DOMAIN}_setari_notificari"
STORAGE_VERSION_NOTIFICATION_SETTINGS: Final = 1

CONF_COURIER: Final = "courier"
CONF_ENABLED_COURIERS: Final = "enabled_couriers"
CONF_INSTANCE_NAME: Final = "instance_name"

CONF_ENTRY_TYPE: Final = "entry_type"
ENTRY_TYPE_ADMIN: Final = "admin"
ENTRY_TYPE_COURIER: Final = "courier"

ADMIN_UNIQUE_ID: Final = f"{DOMAIN}_admin"
ADMIN_ENTRY_TITLE: Final = "Administrare Curieri Romania"

CONF_SAMEDAY_ACCESS_TOKEN: Final = "sameday_access_token"
CONF_SAMEDAY_REFRESH_TOKEN: Final = "sameday_refresh_token"
CONF_SAMEDAY_ID_TOKEN: Final = "sameday_id_token"
CONF_SAMEDAY_TOKEN_EXPIRES_AT: Final = "sameday_token_expires_at"
CONF_SAMEDAY_TOKEN_TYPE: Final = "sameday_token_type"
CONF_SAMEDAY_SCOPE: Final = "sameday_scope"

CONF_FAN_USERNAME: Final = "fan_username"
CONF_FAN_PASSWORD: Final = "fan_password"
CONF_FAN_API_KEY: Final = "fan_api_key"
CONF_FAN_PHONE: Final = "fan_phone"

CONF_CARGUS_PHONE: Final = "cargus_phone"
CONF_CARGUS_ACCESS_TOKEN: Final = "cargus_access_token"
CONF_CARGUS_REFRESH_TOKEN: Final = "cargus_refresh_token"
CONF_CARGUS_TOKEN_EXPIRES_AT: Final = "cargus_token_expires_at"

CONF_GLS_ACCESS_TOKEN: Final = "gls_access_token"
CONF_GLS_REFRESH_TOKEN: Final = "gls_refresh_token"
CONF_GLS_TOKEN_EXPIRES_AT: Final = "gls_token_expires_at"

COURIER_SAMEDAY: Final = "sameday"
COURIER_FAN: Final = "fan_courier"
COURIER_CARGUS: Final = "cargus"
COURIER_GLS: Final = "gls"

COURIER_NAMES: Final = {
    COURIER_SAMEDAY: "Sameday",
    COURIER_FAN: "FAN Courier",
    COURIER_CARGUS: "Cargus",
    COURIER_GLS: "GLS",
}

SUPPORTED_COURIERS: Final = [COURIER_SAMEDAY, COURIER_FAN, COURIER_CARGUS, COURIER_GLS]
PLANNED_COURIERS: Final = []

ATTR_COURIER: Final = "courier"
ATTR_AWB: Final = "awb"
ATTR_DIRECTION: Final = "direction"
ATTR_ORIGINAL_STATUS: Final = "original_status"
ATTR_NORMALIZED_STATUS: Final = "normalized_status"
ATTR_LAST_UPDATE: Final = "last_update"
ATTR_LOCATION: Final = "location"
ATTR_SENDER: Final = "sender"
ATTR_RECIPIENT: Final = "recipient"


CONF_NOTIFICATIONS_ENABLED: Final = "notifications_enabled"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_NOTIFY_NEW_PARCEL: Final = "notify_new_parcel"
CONF_NOTIFY_STATUS_CHANGE: Final = "notify_status_change"
CONF_NOTIFY_OUT_FOR_DELIVERY: Final = "notify_out_for_delivery"
CONF_NOTIFY_PICKUP: Final = "notify_pickup"
CONF_NOTIFY_DELIVERED: Final = "notify_delivered"
CONF_NOTIFY_PROBLEMS: Final = "notify_problems"
CONF_NOTIFY_RETURNED: Final = "notify_returned"
