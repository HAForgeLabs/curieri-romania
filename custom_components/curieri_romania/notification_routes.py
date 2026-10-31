"""Configurare comuna pentru maximum trei destinatii de notificare."""
from __future__ import annotations
import re
from typing import Any

TARGET_FIELDS = ("notify_service", "notify_service_2", "notify_service_3")
ROUTE_MODES = {"inherit", "custom", "disabled"}
PERSISTENT_TARGET = "persistent_notification"


def normalize_target(value: Any) -> str:
    text = str(value or "").strip()
    if text and "." not in text:
        text = f"notify.{text}"
    if text and not re.fullmatch(r"notify\.[a-z0-9_]+", text):
        raise ValueError("Alege un serviciu notify valid pentru telefon.")
    return text


def route_targets(settings: dict[str, Any], entry_id: str = "") -> list[str]:
    """Gol inseamna cont oprit; persistent este o destinatie explicita."""
    routes = settings.get("account_routes", {})
    route = routes.get(entry_id, {}) if isinstance(routes, dict) else {}
    route = route if isinstance(route, dict) else {}
    mode = route.get("mode", "inherit")
    if mode == "disabled":
        return []
    source = route if mode == "custom" else settings
    result = []
    for field in TARGET_FIELDS:
        try:
            target = normalize_target(source.get(field))
        except ValueError:
            continue
        if target and target not in result:
            result.append(target)
    # Un cont custom fara telefon nu revine la telefoanele globale.
    return result or [PERSISTENT_TARGET]


def sanitize_routes(value: Any) -> dict[str, Any]:
    """Recupereaza defensiv configurari partiale fara a schimba contul tinta."""
    result = {}
    if not isinstance(value, dict):
        return result
    for entry_id, route in value.items():
        if not isinstance(entry_id, str) or not isinstance(route, dict):
            continue
        mode = route.get("mode", "inherit")
        clean = {"mode": mode if isinstance(mode, str) and mode in ROUTE_MODES else "inherit"}
        for field in TARGET_FIELDS:
            try:
                clean[field] = normalize_target(route.get(field))
            except ValueError:
                clean[field] = ""
        result[entry_id] = clean
    return result
