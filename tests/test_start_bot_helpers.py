"""Unit tests for start-bot helpers (no live Telegram, no real DROP)."""

from __future__ import annotations

from pathlib import Path

from raundo.testing.start_bot_helpers import align_database_url, tcp_probe


def test_align_database_url_rewrites_port_and_db(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "BOT_TOKEN=\nDATABASE_URL=postgresql+asyncpg://raundo:x@localhost:5432/raundo\n",
        encoding="utf-8",
    )
    desired = "postgresql+asyncpg://raundo:x@127.0.0.1:55432/raundo"
    align_database_url(env, desired)
    text = env.read_text(encoding="utf-8")
    db_line = next(line for line in text.splitlines() if line.startswith("DATABASE_URL="))
    assert db_line.endswith("@127.0.0.1:55432/raundo")
    assert ":5432/" not in db_line
    assert "BOT_TOKEN=" in text


def test_tcp_probe_localhost_redis_or_skip() -> None:
    import socket

    try:
        socket.create_connection(("127.0.0.1", 6379), timeout=1).close()
    except OSError:
        import pytest

        pytest.skip("Redis not listening on 6379")
    tcp_probe("127.0.0.1", 6379, "redis")
