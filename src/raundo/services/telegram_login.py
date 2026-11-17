"""Telegram Login verification for self-service purchases on payments.example.invalid.

The website must be able to identify a buyer without a link from the bot. This
module verifies the Telegram Login Widget payload server-side using the bot
token (never sent to the browser) and keeps a short signed session cookie.

Real payments stay disabled: the workflow is only usable when the owner enables
`SITE_CHECKOUT_ENABLED` after provider approval.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode

from raundo.config import Settings

MAX_AUTH_AGE_SECONDS = 24 * 60 * 60


class TelegramLoginError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _bot_secret(bot_token: str) -> bytes:
    return hashlib.sha256(bot_token.encode("utf-8")).digest()


def verify_telegram_login(data: dict[str, str], settings: Settings) -> dict[str, str]:
    """Validate the widget payload; returns the verified fields."""
    token = (settings.bot_token or "").strip()
    if not token:
        raise TelegramLoginError("bot_token_missing")
    payload = {k: str(v) for k, v in data.items() if k != "hash"}
    received_hash = str(data.get("hash") or "")
    if not received_hash:
        raise TelegramLoginError("hash_missing")
    check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    expected = hmac.new(_bot_secret(token), check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise TelegramLoginError("bad_hash")
    try:
        auth_date = int(payload.get("auth_date", "0"))
    except ValueError as exc:
        raise TelegramLoginError("bad_auth_date") from exc
    if auth_date <= 0 or time.time() - auth_date > MAX_AUTH_AGE_SECONDS:
        raise TelegramLoginError("expired")
    if not payload.get("id", "").isdigit():
        raise TelegramLoginError("bad_id")
    return payload


def _session_key(settings: Settings) -> bytes:
    secret = (settings.site_session_secret or "").strip()
    if secret:
        return secret.encode("utf-8")
    # No separate secret configured: derive a stable key from the bot token.
    return hashlib.sha256(("site-session:" + settings.bot_token).encode("utf-8")).digest()


def issue_session(user_id: str, settings: Settings, *, ttl_seconds: int = 7 * 24 * 3600) -> str:
    body = {"uid": user_id, "exp": int(time.time()) + ttl_seconds}
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(_session_key(settings), raw, hashlib.sha256).digest()
    return f"{urlsafe_b64encode(raw).decode()}." + urlsafe_b64encode(signature).decode()


def read_session(value: str, settings: Settings) -> str | None:
    try:
        raw_b64, sig_b64 = value.split(".", 1)
        raw = urlsafe_b64decode(raw_b64.encode())
        signature = urlsafe_b64decode(sig_b64.encode())
    except (ValueError, TypeError):
        return None
    # Reject non-canonical encodings (for example an appended character after padding).
    if urlsafe_b64encode(raw).decode() != raw_b64:
        return None
    if urlsafe_b64encode(signature).decode() != sig_b64:
        return None
    expected = hmac.new(_session_key(settings), raw, hashlib.sha256).digest()
    if len(signature) != len(expected) or not hmac.compare_digest(expected, signature):
        return None
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    if int(body.get("exp", 0)) < int(time.time()):
        return None
    uid = str(body.get("uid") or "")
    return uid or None
