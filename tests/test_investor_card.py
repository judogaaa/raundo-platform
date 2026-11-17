"""Investor card: human labels, cheque from minor units, full strategy."""

from types import SimpleNamespace

from raundo.cards.investor_card import (
    build_investor_card_view,
    compact_investor_html,
    details_investor_html,
)
from raundo.domain import format_cheque_range


def _profile(**kwargs):  # noqa: ANN003
    base = dict(
        display_name="Дмитрий Волков",
        investor_type="angel",
        organization_name=None,
        position=None,
        support_modes=["direct_equity", "mentorship"],
        strategy_description="Инвестирую в технологические стартапы на стадиях Pre-seed и Seed. "
        * 8,
        min_check_minor=100_000_000,
        max_check_minor=1_500_000_000,
        currency="RUB",
        check_discuss_individually=False,
        geography={"scope": "world"},
        public_reference_url="https://example.com",
        contact_policy="relay",
        telegram_photo_file_id=None,
        review_status="draft",
        claimed_organization_id=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_format_cheque_range_uses_major_units() -> None:
    assert format_cheque_range(100_000_000, 1_500_000_000, "RUB") == "От 1 млн до 15 млн ₽"
    assert format_cheque_range(None, None, "RUB", discuss=True) == "Обсуждаю индивидуально"
    assert "minor" not in format_cheque_range(10_000_00, 20_000_00, "USD").lower()


def test_compact_investor_card_hides_codes() -> None:
    view = build_investor_card_view(
        _profile(),
        sector_codes=["ai_ml", "saas_b2b"],
        stage_codes=["mvp", "launched"],
        include_owner_private=True,
    )
    html = compact_investor_html(view, owner_preview=True)
    details = details_investor_html(view, owner_preview=True)
    assert "ai_ml" not in html
    assert "draft" not in html
    assert "{'scope'" not in html
    assert "relay" not in html
    assert "От 1 млн до 15 млн ₽" in html
    assert "AI/ML" in html
    assert "Весь мир" in html
    assert "Бизнес-ангел" in html
    assert "Орг (заявлено)" not in html
    assert view.strategy in details
    assert len(view.strategy) > 300


def test_org_card_shows_claimed_name_without_verification() -> None:
    view = build_investor_card_view(
        _profile(
            investor_type="vc_fund",
            organization_name="Northwind Capital",
            position="Partner",
            min_check_minor=None,
            max_check_minor=None,
            check_discuss_individually=True,
        ),
        sector_codes=["fintech"],
        stage_codes=["mvp"],
        include_owner_private=False,
    )
    html = compact_investor_html(view)
    assert "Northwind Capital" in html
    assert "affiliation" not in html
    assert "none" not in html
