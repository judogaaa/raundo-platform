"""Support attachments: metadata, honest error codes, no token leakage."""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from raundo.db.session import get_session_factory
from raundo.models.admin_panel import SupportMessage, SupportTicket
from raundo.models.user import User
from raundo.services.accounts import AccountService
from raundo.services.media_proxy import SupportMediaError, fetch_support_attachment
from raundo.services.support_tickets import Attachment, _format_body
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 993_100


def _enabled() -> bool:
    if os.environ.get("RAUNDO_POSTGRES_SUITE_TESTS") != "1":
        return False
    if "raundo_local_suite" not in os.environ.get("DATABASE_URL", ""):
        return False
    assert_suite_db_drop_allowed("raundo_local_suite")
    return True


pytestmark = pytest.mark.skipif(
    not _enabled(), reason="postgres suite disabled (set RAUNDO_POSTGRES_SUITE_TESTS=1)"
)


@pytest.fixture
async def factory():  # noqa: ANN201
    return get_session_factory()


@pytest.fixture(autouse=True)
async def _clean(factory):  # noqa: ANN001, ANN202
    async with factory() as session:
        old = (
            await session.execute(
                select(User.id).where(User.telegram_id >= TG_BASE, User.telegram_id < TG_BASE + 50)
            )
        ).scalars().all()
        if old:
            tickets = (
                await session.execute(
                    select(SupportTicket.id).where(SupportTicket.user_id.in_(old))
                )
            ).scalars().all()
            if tickets:
                await session.execute(
                    delete(SupportMessage).where(SupportMessage.ticket_id.in_(tickets))
                )
                await session.execute(delete(SupportTicket).where(SupportTicket.id.in_(tickets)))
            await session.execute(delete(User).where(User.id.in_(old)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


async def _message(factory, *, file_id: str | None) -> uuid.UUID:  # noqa: ANN001
    async with factory() as session:
        user = await AccountService(session).bind_telegram_user(
            telegram_id=TG_BASE + 1,
            username="support_media_user",
            first_name="Media",
            last_name="User",
            language_code="ru",
        )
        ticket = SupportTicket(
            id=uuid.uuid4(),
            public_number=f"SUP-T-{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            subject="Media",
            status="new",
            priority="normal",
        )
        session.add(ticket)
        await session.flush()
        message = SupportMessage(
            id=uuid.uuid4(),
            ticket_id=ticket.id,
            author_kind="user",
            body="Фото",
            attachment_kind="photo" if file_id else None,
            attachment_size=82093 if file_id else None,
            attachment_file_id=file_id,
        )
        session.add(message)
        await session.commit()
        return message.id


def test_format_body_has_no_system_suffix() -> None:
    attachment = Attachment(kind="photo", file_id="x", size=10)
    assert _format_body("Привет", attachment) == "Привет"
    assert _format_body("Привет", None) == "Привет"


async def test_missing_attachment_is_honest(factory, monkeypatch) -> None:  # noqa: ANN001
    message_id = await _message(factory, file_id=None)
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    async with factory() as session:
        with pytest.raises(SupportMediaError) as exc:
            await fetch_support_attachment(session, message_id)
    assert exc.value.code == "missing"


async def test_missing_token_returns_no_token(factory, monkeypatch) -> None:  # noqa: ANN001
    message_id = await _message(factory, file_id="AgACAgIA-test")
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    async with factory() as session:
        with pytest.raises(SupportMediaError) as exc:
            await fetch_support_attachment(session, message_id)
    assert exc.value.code == "no_token"


class _FakeResponse:
    def __init__(self, payload: dict | None = None, content: bytes = b"", headers: dict | None = None):
        self._payload = payload
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload or {}


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:  # noqa: ANN002
        return None

    async def get(self, url: str, params: dict | None = None, **kwargs):  # noqa: ANN001
        if "getFile" in url:
            return _FakeResponse(payload={"ok": True, "result": {"file_path": "photos/file_1.jpg"}})
        return _FakeResponse(
            content=b"\xff\xd8\xff\xe0real-jpeg-bytes",
            headers={"content-type": "application/octet-stream"},
        )


async def test_real_bytes_and_mime_sniffing(factory, monkeypatch) -> None:  # noqa: ANN001
    message_id = await _message(factory, file_id="AgACAgIA-test")
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    async with factory() as session:
        data, mime = await fetch_support_attachment(session, message_id)
    assert mime == "image/jpeg"
    assert data.startswith(b"\xff\xd8\xff")


class _BrokenClient(_FakeClient):
    async def get(self, url: str, params: dict | None = None, **kwargs):  # noqa: ANN001
        raise httpx.ConnectError("boom")


async def test_telegram_error_is_mapped(factory, monkeypatch) -> None:  # noqa: ANN001
    message_id = await _message(factory, file_id="AgACAgIA-test")
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setattr(httpx, "AsyncClient", _BrokenClient)
    async with factory() as session:
        with pytest.raises(SupportMediaError) as exc:
            await fetch_support_attachment(session, message_id)
    assert exc.value.code == "telegram"


def test_no_token_leak_in_module_source() -> None:
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src"
        / "raundo"
        / "services"
        / "media_proxy.py"
    ).read_text(encoding="utf-8")
    # The token is read server-side and never returned/logged.
    assert "BOT_TOKEN" in source
    assert "return token" not in source
