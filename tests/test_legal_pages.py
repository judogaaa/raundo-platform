"""Public legal edition: content, routes, versioning and closed surface."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from raundo.admin_api.legal_pages import (
    DOC_HREF,
    LEGAL_DIR,
    _approved_prices,
    _manifest,
    render_markdown,
)

SLUGS = [
    "terms",
    "privacy",
    "offer",
    "requisites",
    "payments",
    "data-consent",
    "profile-publication",
    "marketing-consent",
    "image-advertising-consent",
    "moderation",
    "minors",
]


def test_manifest_lists_every_published_document() -> None:
    manifest = _manifest()
    slugs = {doc["slug"] for doc in manifest["docs"]}
    assert set(SLUGS) == slugs
    assert manifest["edition"]
    assert manifest["edition_date"]
    for doc in manifest["docs"]:
        assert (LEGAL_DIR / doc["file"]).is_file(), doc["file"]
        assert DOC_HREF[doc["slug"]].startswith("/legal/")


def test_every_document_has_a_heading_and_is_substantive() -> None:
    manifest = _manifest()
    for doc in manifest["docs"]:
        text = (LEGAL_DIR / doc["file"]).read_text(encoding="utf-8")
        assert text.lstrip().startswith("# "), doc["slug"]
        assert "RAUNDO" in text, doc["slug"]
        assert len(text) > 900, doc["slug"]


def test_render_markdown_basics_and_no_raw_html() -> None:
    rendered = render_markdown(
        "# Заголовок\n\nТекст **жирный** и `код`.\n\n- пункт\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n<script>alert(1)</script>\n"
    )
    assert "<h1>Заголовок</h1>" in rendered
    assert "<strong>жирный</strong>" in rendered
    assert "<code>код</code>" in rendered
    assert "<li>пункт</li>" in rendered
    assert "<table>" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_archive_keeps_previous_edition() -> None:
    manifest = _manifest()
    archive = manifest.get("archive") or []
    assert archive, "previous edition must be archived"
    for entry in archive:
        assert list((LEGAL_DIR / entry["dir"]).glob("*.md"))


@pytest.mark.asyncio
async def test_legal_routes_and_closed_surface() -> None:
    from raundo.admin_api.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://legal.test") as client:
        hub = await client.get("/legal")
        assert hub.status_code == 200
        assert "Редакция" in hub.text
        assert "проект редакции" in hub.text

        for slug in SLUGS:
            page = await client.get(f"/legal/{slug}")
            assert page.status_code == 200, slug
            assert "<h1>" in page.text
            assert "noindex" in page.text

        assert (await client.get("/legal/does-not-exist")).status_code == 404
        assert (await client.get("/legal/terms/extra")).status_code == 404
        # No blanket SPA fallback on the public API surface.
        assert (await client.get("/definitely-not-a-page")).status_code == 404


def test_premium_catalog_is_hidden_until_approved() -> None:
    from raundo.config import Settings

    default = Settings(_env_file=None)
    assert default.premium_catalog_public is False
    asserted = Settings(_env_file=None, PREMIUM_CATALOG_PUBLIC="true")
    assert asserted.premium_catalog_public is True


def test_approved_prices_match_the_code_catalog() -> None:
    from raundo.premium_catalog import PRICES_MINOR

    prices = _approved_prices()
    assert prices["status"] == "approved"
    assert prices["currency"] == "RUB"
    assert prices["approved_at"]
    assert prices["stars"]["status"] == "approved"
    assert prices["plans"] == {plan: dict(durations) for plan, durations in PRICES_MINOR.items()}
    assert sum(len(v) for v in prices["plans"].values()) == 12


def test_stars_prices_registry_matches_code() -> None:
    from raundo.payments_catalog import PROVISIONAL_STARS_PRICES

    prices = _approved_prices()
    stars = prices["stars"]
    assert stars["status"] == "approved"
    assert stars["approved_by"] == "owner"
    assert stars["approved_at"] == "2026-11-04"
    assert stars["plans"] == {
        plan: dict(durations) for plan, durations in PROVISIONAL_STARS_PRICES.items()
    }
    assert sum(len(v) for v in stars["plans"].values()) == 12


def test_prices_shown_only_when_approved() -> None:
    manifest = json.loads((Path(LEGAL_DIR) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "draft_published"
    assert manifest["lawyer_review"] is False


class _FakeResult:
    def __init__(self, rows):  # noqa: ANN001
        self._rows = rows

    def all(self):  # noqa: ANN201
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):  # noqa: ANN001
        self._rows = rows

    async def scalars(self, _stmt):  # noqa: ANN001, ANN201
        return _FakeResult(self._rows)


def _offer(plan_code: str, duration_code: str, duration_hours: int, price_minor: int):
    from raundo.models.billing import PremiumPlanOffer

    return PremiumPlanOffer(
        plan_code=plan_code,
        duration_code=duration_code,
        duration_hours=duration_hours,
        price_minor=price_minor,
        currency="RUB",
        version=1,
        is_active=True,
    )


async def _premium_html(*, public: bool, price_override: int | None = None) -> str:
    from raundo.admin_api import legal_pages
    from raundo.admin_api.app import app
    from raundo.config import Settings, get_settings

    rows = [
        _offer("community_plus", "7d", 168, price_override or 9900),
        _offer("startup_pro", "3d", 72, 14900),
    ]

    async def _fake_session():  # noqa: ANN202
        yield _FakeSession(rows)

    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, PREMIUM_CATALOG_PUBLIC="true" if public else "false"
    )
    app.dependency_overrides[legal_pages._session] = _fake_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://legal.test") as client:
            response = await client.get("/premium-catalog")
            assert response.status_code == 200
            return response.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_premium_page_hides_amounts_by_default_and_shows_approved_ones() -> None:
    hidden = await _premium_html(public=False)
    assert "на утверждении" in hidden
    assert "99 ₽" not in hidden

    shown = await _premium_html(public=True)
    assert "99 ₽" in shown
    assert "149 ₽" in shown
    assert "утверждены владельцем" in shown
    assert "Карта и СБП" in shown
    assert "telegram-widget" in shown
    assert "/api/public/site/orders" in shown
    assert "Перейти к оплате" in shown


@pytest.mark.asyncio
async def test_premium_page_never_shows_an_unapproved_amount() -> None:
    html = await _premium_html(public=True, price_override=12345)
    assert "123 ₽" not in html
    assert "на утверждении" in html

