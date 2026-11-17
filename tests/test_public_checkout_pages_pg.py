"""Public landing page, checkout shell and closed-by-default surface (suite DB only)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from raundo.config import get_settings
from raundo.db.session import get_session_factory
from raundo.models.payments import PaymentOrder, PaymentTransaction
from raundo.models.system import OutboxEvent
from raundo.models.user import User
from raundo.testing.db_safety import assert_suite_db_drop_allowed
from tests.test_payments_api_pg import SETTINGS, TG_BASE, _client, _order


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
async def _clean_test_user(factory):  # noqa: ANN001, ANN202
    """Remove the payer created by _order so the suite DB stays reproducible."""
    yield
    async with factory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == TG_BASE + 1))
        if user is not None:
            order_ids = (
                await session.execute(
                    select(PaymentOrder.id).where(PaymentOrder.user_id == user.id)
                )
            ).scalars().all()
            if order_ids:
                await session.execute(
                    delete(PaymentTransaction).where(PaymentTransaction.order_id.in_(order_ids))
                )
                await session.execute(
                    delete(OutboxEvent).where(
                        OutboxEvent.resource_type == "payment_order",
                        OutboxEvent.recipient_user_id == user.id,
                    )
                )
                await session.execute(delete(PaymentOrder).where(PaymentOrder.id.in_(order_ids)))
            await session.execute(delete(User).where(User.id == user.id))
            await session.commit()

    # The suite engine is bound to this test's event loop; rebuild it next time.
    from raundo.db import session as db_session_module

    cached = db_session_module._cached_factory
    if cached is not None:
        engine = cached.kw.get("bind")
        if engine is not None:
            await engine.dispose()
        db_session_module._cached_factory = None


def _dist(tmp_path):  # noqa: ANN001, ANN202
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "landing.html").write_text(
        "<!doctype html><html><head><title>RAUNDO — оплата подписки</title></head>"
        "<body><h1>Оплата подписки</h1></body></html>",
        encoding="utf-8",
    )
    (dist / "checkout.html").write_text(
        '<!doctype html><html><body><div id="checkout-root"></div></body></html>',
        encoding="utf-8",
    )
    (dist / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    return dist


@pytest.mark.asyncio
async def test_public_landing_is_branded_and_surface_stays_closed(factory, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from raundo.admin_api import public_payments
    from raundo.admin_api.app import app

    monkeypatch.setattr(public_payments, "CHECKOUT_DIST", _dist(tmp_path))

    async with _client() as client:
        landing = await client.get("/")
        assert landing.status_code == 200
        assert "Оплата подписки" in landing.text
        assert landing.headers["cache-control"].startswith("no-store")
        assert landing.headers["x-content-type-options"] == "nosniff"

        logo = await client.get("/logo.png")
        assert logo.status_code == 200
        assert logo.headers["content-type"] == "image/png"

        # No catch-all: unrelated paths stay 404 on the public API surface.
        assert (await client.get("/definitely-not-a-page")).status_code == 404

        bare = await client.get("/checkout", follow_redirects=False)
        assert bare.status_code == 302
        assert bare.headers["location"] == "/"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_checkout_page_requires_a_real_order_token(factory, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from raundo.admin_api import public_payments
    from raundo.admin_api.app import app

    monkeypatch.setattr(public_payments, "CHECKOUT_DIST", _dist(tmp_path))
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    try:
        order_id, token, _user_id = await _order(factory)

        async with _client() as client:
            page = await client.get(f"/checkout/{token}")
            assert page.status_code == 200
            assert "checkout-root" in page.text
            assert page.headers["referrer-policy"] == "no-referrer"

            unknown = await client.get("/checkout/not-a-real-token")
            assert unknown.status_code == 404

        async with factory() as session:
            order = await session.get(PaymentOrder, order_id)
            assert order is not None
            order.checkout_token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await session.commit()

        async with _client() as client:
            expired = await client.get(f"/checkout/{token}")
            assert expired.status_code == 410
            # The public data endpoint reports the same state.
            assert (await client.get(f"/api/public/checkout/{token}")).status_code == 410
    finally:
        app.dependency_overrides.clear()
