"""Telegram egress policy: proxy required means no silent direct fallback."""

from __future__ import annotations

import asyncio

import pytest

from raundo.config import Settings
from raundo.telegram_transport import (
    TelegramProxyError,
    api_environment,
    build_bot,
    ensure_proxy_policy,
    normalize_proxy,
    resolve_api_server,
)


def _settings(**kwargs) -> Settings:  # noqa: ANN003
    return Settings(_env_file=None, **kwargs)


def test_normalize_proxy_accepts_host_port_and_http() -> None:
    assert normalize_proxy("") is None
    assert normalize_proxy("   ") is None
    assert normalize_proxy("vpn:1080") == "http://vpn:1080"
    assert normalize_proxy("http://vpn:1080") == "http://vpn:1080"
    assert normalize_proxy("HTTPS://vpn:8443") == "HTTPS://vpn:8443"


def test_normalize_proxy_rejects_socks_only() -> None:
    with pytest.raises(TelegramProxyError):
        normalize_proxy("socks5://vpn:1080")


def test_require_proxy_without_proxy_is_fatal() -> None:
    settings = _settings(TELEGRAM_REQUIRE_PROXY="true")
    with pytest.raises(TelegramProxyError):
        ensure_proxy_policy(settings)


def test_require_proxy_off_allows_direct() -> None:
    assert ensure_proxy_policy(_settings()) is None


def test_proxy_is_returned_when_configured() -> None:
    settings = _settings(TELEGRAM_PROXY="vpn:1080", TELEGRAM_REQUIRE_PROXY="true")
    assert ensure_proxy_policy(settings) == "http://vpn:1080"


def test_bot_session_uses_proxy_when_set() -> None:
    pytest.importorskip("aiohttp_socks", reason="aiogram needs aiohttp-socks for proxy sessions")
    bot = build_bot("123456789:TEST_TOKEN_FOR_UNIT", parse_mode=None, proxy="http://vpn:1080")
    try:
        assert getattr(bot.session, "_proxy", None) == "http://vpn:1080"
    finally:
        asyncio.run(bot.session.close())


def test_bot_without_proxy_has_no_proxy() -> None:
    bot = build_bot("123456789:TEST_TOKEN_FOR_UNIT", parse_mode=None, proxy=None)
    try:
        assert getattr(bot.session, "_proxy", None) is None
    finally:
        asyncio.run(bot.session.close())


def test_api_server_switches_between_production_and_test_environment() -> None:
    assert resolve_api_server(False).base == "https://api.telegram.org/bot{token}/{method}"
    assert resolve_api_server(True).base == "https://api.telegram.org/bot{token}/test/{method}"
    assert resolve_api_server(True).file.endswith("/test/{path}")


def test_test_mode_bot_talks_to_test_environment() -> None:
    bot = build_bot("123456789:TEST_TOKEN_FOR_UNIT", parse_mode=None, test_mode=True)
    try:
        assert api_environment(bot) == "test"
        # The /test/ path is what isolates test users from real users.
        assert "/test/" in bot.session.api.base
    finally:
        asyncio.run(bot.session.close())


def test_production_bot_never_uses_test_path() -> None:
    bot = build_bot("123456789:TEST_TOKEN_FOR_UNIT", parse_mode=None)
    try:
        assert api_environment(bot) == "production"
        assert "/test/" not in bot.session.api.base
    finally:
        asyncio.run(bot.session.close())


def test_test_mode_keeps_proxy_egress() -> None:
    pytest.importorskip("aiohttp_socks", reason="aiogram needs aiohttp-socks for proxy sessions")
    bot = build_bot(
        "123456789:TEST_TOKEN_FOR_UNIT",
        parse_mode=None,
        proxy="http://vpn:1080",
        test_mode=True,
    )
    try:
        assert api_environment(bot) == "test"
        assert getattr(bot.session, "_proxy", None) == "http://vpn:1080"
    finally:
        asyncio.run(bot.session.close())
