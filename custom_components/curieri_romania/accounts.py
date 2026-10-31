"""Identificarea locala a conturilor deja validate de provider."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from .const import (
    CONF_ACCOUNT_ID,
    CONF_ACCOUNT_ALIASES,
    CONF_CARGUS_ACCESS_TOKEN,
    CONF_CARGUS_EMAIL,
    CONF_CARGUS_PHONE,
    CONF_FAN_USERNAME,
    CONF_FAN_USER_ID,
    CONF_GLS_ACCESS_TOKEN,
    COURIER_CARGUS,
    COURIER_FAN,
    COURIER_GLS,
)


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    """Identitate pseudonimizata; tokenurile nu sunt folosite ca identificator."""

    account_id: str
    authoritative: bool
    aliases: frozenset[str]


def _account_hash(courier: str, kind: str, value: str) -> str:
    return hashlib.sha256(f"{courier}|{kind}|{value}".encode()).hexdigest()


def _subject(value: Any) -> str:
    """Accepta doar valori scalare; identifica defensiv raspunsuri incomplete."""

    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return str(value).strip()


def _jwt_claims(token: Any) -> dict[str, Any]:
    """Citeste metadate locale DUPA validarea tokenului prin API, nu il valideaza."""

    value = str(token or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    parts = value.split(".")
    if len(parts) != 3 or len(parts[1]) > 32768:
        return {}
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(raw)
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        return {}
    return claims if isinstance(claims, dict) else {}


def _phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "40" + digits[1:]
    if len(digits) == 9 and digits.startswith("7"):
        digits = "40" + digits
    return digits


def identify_account(courier: str, data: dict[str, Any], hint: str = "") -> AccountIdentity:
    """Deriva identitatea din userId, subject JWT sau loginul existent."""

    saved_aliases = data.get(CONF_ACCOUNT_ALIASES)
    aliases: set[str] = {
        value for value in saved_aliases[:32]
        if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)
    } if isinstance(saved_aliases, list) else set()

    def alias(kind: str, value: Any) -> None:
        clean = _subject(value).casefold()
        if clean:
            aliases.add(_account_hash(courier, kind, clean))

    subject = ""
    if courier == COURIER_FAN:
        subject = _subject(data.get(CONF_FAN_USER_ID))
        alias("login", data.get(CONF_FAN_USERNAME))
    elif courier in {COURIER_CARGUS, COURIER_GLS}:
        token_key = CONF_CARGUS_ACCESS_TOKEN if courier == COURIER_CARGUS else CONF_GLS_ACCESS_TOKEN
        claims = _jwt_claims(data.get(token_key))
        for key in ("oid", "sub", "user_id", "userId", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier"):
            subject = _subject(claims.get(key))
            if subject:
                break
        for key in ("email", "preferred_username", "signInName"):
            alias("login", claims.get(key))
        emails = claims.get("emails")
        if isinstance(emails, list):
            for email in emails:
                alias("login", email)
        if courier == COURIER_CARGUS:
            alias("login", data.get(CONF_CARGUS_EMAIL))
            alias("phone", _phone(data.get(CONF_CARGUS_PHONE)))
        alias("login", hint)

    if subject:
        return AccountIdentity(_account_hash(courier, "subject", subject), True, frozenset(aliases))
    # Fallback exclusiv pe login stabil; niciodata pe refresh tokenul rotativ.
    stored = _subject(data.get(CONF_ACCOUNT_ID))
    fallback = stored or (sorted(aliases)[0] if aliases else "")
    return AccountIdentity(fallback, False, frozenset(aliases))


def same_account(left: AccountIdentity, right: AccountIdentity) -> bool:
    """Subiectele confirmate au prioritate fata de telefon/email comun."""

    if left.account_id and left.account_id == right.account_id:
        return True
    if left.authoritative and right.authoritative:
        return False
    return bool(left.aliases & right.aliases)
