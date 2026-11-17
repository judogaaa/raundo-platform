"""Helpers for scripts/start-bot.ps1. Never print BOT_TOKEN."""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
from pathlib import Path


def align_database_url(env_path: Path, desired_url: str) -> None:
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    lines = text.splitlines()
    target = f"DATABASE_URL={desired_url}"
    out: list[str] = []
    changed = False
    found = False
    for line in lines:
        if line.startswith("DATABASE_URL="):
            found = True
            if line.strip() != target:
                out.append(target)
                changed = True
            else:
                out.append(line)
        else:
            out.append(line)
    if not found:
        out.append(target)
        changed = True
    if changed:
        ending = "\n" if text.endswith("\n") or not text else ""
        env_path.write_text("\n".join(out) + ending, encoding="utf-8")
    print("database_url_aligned")


def tcp_probe(host: str, port: int, label: str) -> None:
    sock = socket.create_connection((host, port), timeout=5)
    sock.close()
    print(f"{label}_tcp_ok")


def token_status(*, expected_db: str, expected_port: int) -> int:
    # Import after optional env overrides from caller.
    from raundo.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    present = bool(settings.bot_token and settings.bot_token.strip())
    print(f"present={present}")
    print(f"len={len((settings.bot_token or '').strip())}")
    print(f"db_is_app={settings.database_url.rstrip('/').endswith('/' + expected_db)}")
    print(f"db_port_ok={(':' + str(expected_port) + '/') in settings.database_url}")
    print(f"mode={settings.bot_mode}")
    if not present:
        return 2
    if not settings.database_url.rstrip("/").endswith("/" + expected_db):
        return 1
    if (":" + str(expected_port) + "/") not in settings.database_url:
        return 1
    return 0


async def _get_me() -> None:
    from raundo.config import get_settings
    from raundo.telegram_transport import build_bot as build_telegram_bot
    from raundo.telegram_transport import ensure_proxy_policy

    settings = get_settings()
    proxy = ensure_proxy_policy(settings)
    bot = build_telegram_bot(settings.bot_token, parse_mode=None, proxy=proxy)
    try:
        me = await bot.get_me()
        print(f"username={me.username or ''}")
        print(f"id_set={bool(me.id)}")
        print(f"egress={'proxy' if proxy else 'direct'}")
        print("api_ok=True")
    finally:
        await bot.session.close()


def telegram_get_me() -> int:
    try:
        asyncio.run(_get_me())
        return 0
    except Exception as exc:  # noqa: BLE001 — surface class only, never token
        print(f"api_ok=False error_type={type(exc).__name__}")
        return 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_align = sub.add_parser("align-db-url")
    p_align.add_argument("--env", type=Path, required=True)
    p_align.add_argument("--url", required=True)

    p_tcp = sub.add_parser("tcp")
    p_tcp.add_argument("--host", default="127.0.0.1")
    p_tcp.add_argument("--port", type=int, required=True)
    p_tcp.add_argument("--label", required=True)

    p_token = sub.add_parser("token-status")
    p_token.add_argument("--db", required=True)
    p_token.add_argument("--port", type=int, required=True)

    sub.add_parser("telegram-get-me")

    args = parser.parse_args(argv)
    if args.cmd == "align-db-url":
        align_database_url(args.env, args.url)
        return 0
    if args.cmd == "tcp":
        tcp_probe(args.host, args.port, args.label)
        return 0
    if args.cmd == "token-status":
        return token_status(expected_db=args.db, expected_port=args.port)
    if args.cmd == "telegram-get-me":
        return telegram_get_me()
    return 1


if __name__ == "__main__":
    sys.exit(main())
