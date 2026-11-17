"""Telegram Bot API transport.

In production the Russian VPS cannot reach Telegram directly, so every Bot API
call is routed through a local proxy (sing-box inside the Compose network).
The transport is strict: if `TELEGRAM_REQUIRE_PROXY` is set and no proxy is
configured, the process refuses to start instead of silently going direct.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION, TEST, TelegramAPIServer
from aiogram.enums import ParseMode

from raundo.config import Settings

ALLOWED_PROXY_SCHEMES = ("http://", "https://")


class TelegramProxyError(RuntimeError):
    """Telegram egress configuration is invalid or missing."""


def normalize_proxy(raw: str | None) -> str | None:
    """Return a usable proxy URL or None. Accepts `host:port` without a scheme."""
    value = (raw or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = f"http://{value}"
    if not value.lower().startswith(ALLOWED_PROXY_SCHEMES):
        raise TelegramProxyError(
            "TELEGRAM_PROXY must be an http(s) proxy (sing-box mixed inbound). "
            "SOCKS-only proxies are not supported by the aiogram HTTP session."
        )
    return value


def ensure_proxy_policy(settings: Settings) -> str | None:
    """Apply the fail-closed rule and return the proxy URL (or None)."""
    proxy = normalize_proxy(settings.telegram_proxy)
    if proxy is None and settings.telegram_require_proxy:
        raise TelegramProxyError(
            "TELEGRAM_REQUIRE_PROXY is enabled but TELEGRAM_PROXY is empty: "
            "refusing to start Telegram transport without an egress proxy."
        )
    return proxy


def resolve_api_server(test_mode: bool) -> TelegramAPIServer:
    """Return the Bot API server for the requested environment.

    The test server is Telegram's dedicated test environment: all methods go to
    `https://api.telegram.org/bot<token>/test/<method>`, which only knows test
    users, test chats and test Stars. It is never a local stub.
    """
    return TEST if test_mode else PRODUCTION


def api_environment(bot: Bot) -> str:
    """Label the Bot API server a bot is talking to (`test` or `production`)."""
    api = getattr(bot.session, "api", None)
    base = getattr(api, "base", "") or ""
    return "test" if "/test/" in base else "production"


def build_bot(
    token: str,
    *,
    parse_mode: ParseMode | None,
    proxy: str | None = None,
    test_mode: bool = False,
) -> Bot:
    """Create a Bot whose session uses the proxy when one is configured.

    AiohttpSession raises on proxy failures; aiogram never retries around the
    session, so a dead proxy produces errors instead of a direct fallback.
    `test_mode=True` targets the Telegram test environment instead of production.
    """
    api = resolve_api_server(test_mode)
    session = AiohttpSession(api=api, proxy=proxy) if (proxy or test_mode) else None
    kwargs: dict[str, object] = {
        "token": token,
        "default": DefaultBotProperties(parse_mode=parse_mode),
    }
    if session is not None:
        kwargs["session"] = session
    return Bot(**kwargs)  # type: ignore[arg-type]
