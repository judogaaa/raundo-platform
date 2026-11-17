"""Demo media scheme: local files for showcase, safe path handling."""

from __future__ import annotations

import os
import uuid

import pytest
from PIL import Image
from sqlalchemy import delete, select

from raundo.db.session import get_session_factory
from raundo.models.startup import Startup, StartupMedia
from raundo.models.user import User
from raundo.routers.feed import _photo_source
from raundo.services.accounts import AccountService
from raundo.services.media_proxy import _demo_media_path, fetch_startup_photo
from raundo.testing.db_safety import assert_suite_db_drop_allowed

TG_BASE = 993_500
DEMO_DIR = __import__("pathlib").Path(__file__).resolve().parents[1] / "private_media" / "demo"


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


def test_demo_path_guards_traversal() -> None:
    assert _demo_media_path("demo:../secrets.png") is None
    assert _demo_media_path("demo:..\\secrets.png") is None
    assert _demo_media_path("demo:") is None
    assert _demo_media_path("AgACAgIA-real-telegram-file") is None


def test_photo_source_modes() -> None:
    assert _photo_source(None) is None
    assert _photo_source("AgACAgIA-real") == "AgACAgIA-real"
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    asset = DEMO_DIR / "unit-demo.png"
    Image.new("RGB", (40, 30), (40, 120, 90)).save(asset, "PNG")
    try:
        source = _photo_source("demo:unit-demo.png")
        assert source is not None and not isinstance(source, str)
    finally:
        asset.unlink(missing_ok=True)


@pytest.fixture
async def factory():  # noqa: ANN201
    return get_session_factory()


@pytest.fixture(autouse=True)
async def _clean(factory):  # noqa: ANN001, ANN202
    async with factory() as session:
        old = (
            await session.execute(
                select(User.id).where(User.telegram_id >= TG_BASE, User.telegram_id < TG_BASE + 20)
            )
        ).scalars().all()
        if old:
            await session.execute(delete(StartupMedia).where(StartupMedia.startup_id.in_(
                select(Startup.id).where(Startup.owner_user_id.in_(old))
            )))
            await session.execute(delete(Startup).where(Startup.owner_user_id.in_(old)))
            await session.execute(delete(User).where(User.id.in_(old)))
        await session.commit()
    yield
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()


@pytest.mark.asyncio
async def test_demo_startup_photo_is_served_from_local_asset(factory) -> None:  # noqa: ANN001
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    asset = DEMO_DIR / "unit-fetch.png"
    Image.new("RGB", (64, 48), (20, 80, 160)).save(asset, "PNG")
    try:
        async with factory() as session:
            owner = await AccountService(session).bind_telegram_user(
                telegram_id=TG_BASE + 1,
                username="demo_media_owner",
                first_name="Demo",
                last_name="Media",
                language_code="ru",
            )
            startup = Startup(
                id=uuid.uuid4(),
                owner_user_id=owner.id,
                slug=f"demo-media-{uuid.uuid4().hex[:8]}",
                name="ДЕМО · Media",
                review_state="submitted",
                current_revision=1,
            )
            session.add(startup)
            await session.flush()
            session.add(
                StartupMedia(
                    id=uuid.uuid4(),
                    startup_id=startup.id,
                    position=1,
                    kind="photo",
                    telegram_file_id="demo:unit-fetch.png",
                    file_unique_id="demo-unit",
                    scan_state="accepted",
                )
            )
            await session.commit()
            data, mime = await fetch_startup_photo(session, startup.id, 1)
        assert mime == "image/png"
        assert data.startswith(b"\x89PNG")
    finally:
        asset.unlink(missing_ok=True)
