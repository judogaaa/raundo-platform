"""Unit tests for compact / details startup cards (no live Telegram)."""

from __future__ import annotations

from types import SimpleNamespace

from raundo.cards.startup_card import (
    _pitch_compact,
    _sectors_meta_line,
    build_startup_card_view,
    compact_startup_html,
    cover_needs_letterbox,
    details_startup_html,
)
from raundo.domain import (
    DESC_MAX,
    DESC_MIN,
    PITCH_MAX,
    PITCH_MIN,
    apply_investment_ask_step,
    format_ask_amount,
    validate_description,
    validate_pitch,
)
from raundo.wizard_keyboards import BTN_CLEAR_ASK, BTN_SKIP, preview_nav

_GOOD_PITCH = (
    "IMVERA звонит пожилым людям на обычный телефон и сообщает семье, "
    "когда близкому требуется внимание."
)


def test_format_ask_amount_human_rub() -> None:
    assert format_ask_amount(1_500_000_000, "RUB") == "15 млн ₽"
    assert format_ask_amount(None, "RUB") is None
    assert "minor" not in (format_ask_amount(10_000_00, "RUB") or "").lower()


def test_pitch_limits_50_to_120_with_legacy_keep() -> None:
    assert validate_pitch("x" * (PITCH_MIN - 1)) is not None
    assert validate_pitch("x" * PITCH_MIN) is None
    assert validate_pitch("x" * PITCH_MAX) is None
    assert validate_pitch("x" * (PITCH_MAX + 1)) is not None
    legacy = "z" * 240
    assert validate_pitch(legacy, previous=legacy) is None
    assert validate_pitch("z" * 200, previous=legacy) is not None
    assert PITCH_MIN <= len(_GOOD_PITCH) <= PITCH_MAX


def test_skip_preserves_investment_ask_clear_removes() -> None:
    kept = apply_investment_ask_step(
        BTN_SKIP,
        current_minor=1_500_000_000,
        current_currency="RUB",
        clear_label=BTN_CLEAR_ASK,
        skip_label=BTN_SKIP,
    )
    assert kept == (1_500_000_000, "RUB")
    empty_skip = apply_investment_ask_step(
        BTN_SKIP,
        current_minor=None,
        current_currency=None,
        clear_label=BTN_CLEAR_ASK,
        skip_label=BTN_SKIP,
    )
    assert empty_skip == (None, None)
    cleared = apply_investment_ask_step(
        BTN_CLEAR_ASK,
        current_minor=1_500_000_000,
        current_currency="RUB",
        clear_label=BTN_CLEAR_ASK,
        skip_label=BTN_SKIP,
    )
    assert cleared == (None, None)
    changed = apply_investment_ask_step(
        "20000000 RUB",
        current_minor=1_500_000_000,
        current_currency="RUB",
        clear_label=BTN_CLEAR_ASK,
        skip_label=BTN_SKIP,
    )
    assert changed == (2_000_000_000, "RUB")
    assert (
        apply_investment_ask_step(
            "not-money",
            current_minor=1,
            current_currency="RUB",
            clear_label=BTN_CLEAR_ASK,
            skip_label=BTN_SKIP,
        )
        is None
    )


def test_description_limits_100_to_500_with_legacy_keep() -> None:
    assert validate_description("x" * (DESC_MIN - 1)) is not None
    assert validate_description("x" * DESC_MIN) is None
    assert validate_description("x" * DESC_MAX) is None
    assert validate_description("x" * (DESC_MAX + 1)) is not None
    legacy = "y" * 900
    assert validate_description(legacy, previous=legacy) is None
    assert validate_description(legacy[:-1], previous=legacy) is not None


def _startup(**kwargs):  # noqa: ANN003
    base = dict(
        name="IMVERA",
        one_liner=_GOOD_PITCH,
        full_description="B" * 120,
        website_url="https://imvera.ru",
        app_url=None,
        demo_url=None,
        no_link_reason=None,
        ask_amount_minor=1_500_000_000,
        ask_currency="RUB",
        intents=["investment"],
        audience_codes=[],
        audience_note=None,
        progress_codes=[],
        monetization_status=None,
        monetization_note=None,
        fundraising_stage=None,
        use_of_funds_codes=[],
        use_of_funds_note=None,
        country=None,
        review_state="draft",
        founder_display_name="Ivan",
        founder_role="CEO",
        contact_policy="relay",
        contact_username="secret_founder",
        media=[],
        sectors=[],
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_compact_shows_two_or_three_sector_names_not_plus_n() -> None:
    startup = _startup()
    view = build_startup_card_view(
        startup,
        sector_labels=["HealthTech", "AI/ML", "Consumer"],
        stage_label="MVP",
        include_owner_private=False,
    )
    html = compact_startup_html(view, owner_preview=False)
    assert "<b>🚀 IMVERA</b>" in html
    assert "HealthTech · AI/ML · Consumer · MVP" in html
    assert "+2" not in html
    assert "15 млн ₽" in html
    assert "Инвестиции" in html
    assert "imvera.ru" in html
    assert "https://" not in html
    assert "B" * 20 not in html
    assert "secret_founder" not in html
    assert "Ivan" not in html
    assert "1 фото" not in html
    assert "draft" not in html
    assert "статус" not in html.lower()
    assert "minor" not in html.lower()
    assert "🚀" in html


def test_valid_pitch_shown_in_full_without_ellipsis() -> None:
    view = build_startup_card_view(
        _startup(one_liner=_GOOD_PITCH, intents=[], ask_amount_minor=None),
        sector_labels=["HealthTech"],
        stage_label="MVP",
    )
    html = compact_startup_html(view)
    assert _GOOD_PITCH in html
    assert "…" not in html


def test_pitch_legacy_truncate_helper_does_not_cut_mid_word() -> None:
    pitch = (
        "Первое предложение здесь полностью. "
        "Второе предложение тоже важное для смысла карточки проекта."
    )
    out = _pitch_compact(pitch, limit=70)
    assert "…" in out
    assert not out.rstrip("…").endswith(("перв", "предложени", "важн"))
    assert out.startswith("Первое предложение здесь полностью.")


def test_sectors_meta_prefers_names_then_stage() -> None:
    assert _sectors_meta_line(["HealthTech"], "MVP") == "HealthTech · MVP"
    assert _sectors_meta_line(["A", "B", "C"], "MVP") == "A · B · C · MVP"
    assert _sectors_meta_line([], "MVP") == "MVP"


def test_cover_letterbox_heuristic() -> None:
    assert cover_needs_letterbox(462, 471) is True
    assert cover_needs_letterbox(1600, 900) is False
    assert cover_needs_letterbox(None, None) is True


def test_details_shows_all_sectors_and_full_description() -> None:
    startup = _startup(full_description="D" * 200)
    view = build_startup_card_view(
        startup,
        sector_labels=["AI", "HealthTech", "SaaS"],
        stage_label="MVP",
        include_owner_private=False,
    )
    details = details_startup_html(view, owner_preview=False)
    assert "AI · HealthTech · SaaS" in details
    assert "D" * 200 in details
    assert "Команда" in details
    assert "Ivan · CEO" in details
    assert "secret_founder" not in details
    assert "Контакт после согласия" not in details


def test_owner_preview_compact_still_hides_tech_chrome() -> None:
    startup = _startup(
        media=[SimpleNamespace(position=1, telegram_file_id="f", width=100, height=100)]
    )
    view = build_startup_card_view(
        startup,
        sector_labels=["HealthTech", "AI/ML"],
        stage_label="MVP",
        include_owner_private=True,
    )
    html = compact_startup_html(view, owner_preview=True)
    assert "1 фото" not in html
    assert "draft" not in html
    assert "secret_founder" not in html


def test_preview_keyboard_unchanged() -> None:
    """Regression: owner-approved ReplyKeyboard layout must stay intact."""
    markup = preview_nav()
    rows = [[btn.text for btn in row] for row in markup.keyboard]
    assert rows == [
        ["✅ Отправить на проверку"],
        ["Подробнее"],
        ["✏️ Редактировать анкету", "💾 Черновик"],
        ["❌ Отмена"],
    ]


def test_cover_file_id_from_media_order() -> None:
    m1 = SimpleNamespace(position=2, telegram_file_id="file_second", width=100, height=100)
    m2 = SimpleNamespace(position=1, telegram_file_id="file_first", width=100, height=100)
    startup = _startup(media=[m1, m2])
    view = build_startup_card_view(startup, sector_labels=["AI"], stage_label=None)
    assert view.cover_file_id == "file_first"
    assert view.gallery_file_ids == ("file_first", "file_second")
    assert view.photo_count == 2


def test_details_full_monetization_no_link_reason_human_contact() -> None:
    note = (
        "Планируем зарабатывать на ежемесячной B2B-подписке для кафе и ресторанов. "
        "Клиенты будут платить за прогнозирование спроса, оптимизацию закупок "
        "и сокращение списаний."
    )
    assert len(note) <= 240
    view = build_startup_card_view(
        _startup(
            website_url=None,
            no_link_reason="Сайт и демоверсия ещё не разработаны",
            monetization_status="planned",
            monetization_note=note,
            ask_amount_minor=500_000_000,
            contact_policy="relay",
        ),
        sector_labels=["SaaS"],
        stage_label="Идея",
        include_owner_private=True,
    )
    compact = compact_startup_html(view, owner_preview=True)
    details = details_startup_html(view, owner_preview=True)
    assert "Модель спланирована, выручки нет" in compact
    assert note not in compact
    assert note in details
    assert "relay" not in details.lower()
    assert "Через RAUNDO после подтверждения запроса" in details
    assert "пока нет" in details
    assert "Сайт и демоверсия ещё не разработаны" in details
    assert view.ask_display == "Инвестиции: 5 млн ₽"


def test_no_photo_card_is_text_only_html() -> None:
    view = build_startup_card_view(
        _startup(media=[]),
        sector_labels=["HealthTech"],
        stage_label="MVP",
    )
    html = compact_startup_html(view)
    assert view.cover_file_id is None
    assert "<b>🚀 IMVERA</b>" in html


def test_incomplete_draft_omits_empty_investor_facts() -> None:
    """Legacy / partial draft: no fake audience, progress, monetization, or traction."""
    view = build_startup_card_view(
        _startup(
            audience_codes=[],
            progress_codes=[],
            monetization_status=None,
            ask_amount_minor=None,
            intents=[],
        ),
        sector_labels=["HealthTech", "AI/ML"],
        stage_label="MVP",
    )
    html = compact_startup_html(view)
    assert "Аудитория" not in html
    assert "Клиенты" not in html
    assert "Прогресс" not in html
    assert "Монетизация" not in html
    assert "Ищем" not in html
    assert "выручк" not in html.lower()
    assert "продаж" not in html.lower()
    assert "пользовател" not in html.lower()
    assert len(html) <= 1024


def test_mvp_only_does_not_imply_pmf_or_revenue() -> None:
    view = build_startup_card_view(
        _startup(
            audience_codes=["b2c"],
            progress_codes=["mvp"],
            monetization_status="not_yet",
            ask_amount_minor=None,
            intents=["feedback"],
        ),
        sector_labels=["HealthTech"],
        stage_label="MVP",
    )
    html = compact_startup_html(view)
    assert "<b>🎯 Аудитория</b>" in html
    assert "B2C" in html
    assert "<b>📈 Прогресс</b>" in html
    assert "MVP / рабочий продукт" in html
    assert "Пока не монетизируем" in html
    assert "выручк" not in html.lower()
    assert "product-market" not in html.lower()
    assert "15 млн" not in html


def test_pilots_and_planned_monetization_readable() -> None:
    view = build_startup_card_view(
        _startup(
            audience_codes=["b2b_smb"],
            progress_codes=["mvp", "pilots"],
            monetization_status="planned",
            monetization_note="B2C-подписка",
            intents=["pilots", "partners"],
            ask_amount_minor=None,
            fundraising_stage="pre_seed",
        ),
        sector_labels=["SaaS"],
        stage_label="Пилот",
    )
    html = compact_startup_html(view)
    details = details_startup_html(view)
    assert "Пилоты" in html
    assert "Модель спланирована, выручки нет" in html
    assert "B2C-подписка" not in html
    assert "B2C-подписка" in details
    assert "Уже зарабатываем" not in html
    assert "Подтверждённая выручка" not in html


def test_active_revenue_shown_only_when_status_active() -> None:
    view = build_startup_card_view(
        _startup(
            audience_codes=["b2b_enterprise"],
            progress_codes=["mvp", "users", "revenue"],
            monetization_status="active",
            monetization_note="Годовая лицензия",
            intents=["investment"],
            fundraising_stage="seed",
            use_of_funds_codes=["product", "growth"],
            country="Россия",
        ),
        sector_labels=["Enterprise"],
        stage_label="Рост",
    )
    compact = compact_startup_html(view)
    details = details_startup_html(view)
    assert "Уже зарабатываем" in compact
    assert "Годовая лицензия" not in compact
    assert "Годовая лицензия" in details
    assert "15 млн ₽" in compact
    assert "Инвестиции" in compact
    assert "Раунд" in details and "Seed" in details
    assert "Инвестиционный запрос" in details
    assert "Использование средств" in details
    assert "Продукт" in details and "Рост и маркетинг" in details
    assert "Россия" in details


def test_ask_hides_contradictory_not_raising_label() -> None:
    view = build_startup_card_view(
        _startup(fundraising_stage="not_raising", ask_amount_minor=1_500_000_000),
        sector_labels=["AI"],
        stage_label="MVP",
    )
    assert view.ask_label == "15 млн ₽"
    assert view.fundraising_line is None
    details = details_startup_html(view)
    assert "не привлекаем" not in details.lower()


def test_multi_sector_and_seeking_on_compact() -> None:
    view = build_startup_card_view(
        _startup(
            audience_codes=["b2c", "b2b_enterprise"],
            progress_codes=["prototype"],
            monetization_status="not_yet",
            intents=["investment", "pilots", "partners"],
            ask_amount_minor=None,
        ),
        sector_labels=["HealthTech", "AI/ML", "Consumer"],
        stage_label="Идея",
    )
    html = compact_startup_html(view)
    assert "HealthTech · AI/ML · Consumer · Идея" in html
    assert "B2C · B2B Enterprise" in html
    assert "B2C · B2B · Enterprise" not in html
    assert "Инвестиции, Пилоты, Партнёры" in html
    assert len(html) <= 1024


def test_none_yet_progress_dropped_when_stronger_signal_present() -> None:
    view = build_startup_card_view(
        _startup(progress_codes=["none_yet", "mvp"]),
        sector_labels=["AI"],
        stage_label="MVP",
    )
    assert view.progress_line == "MVP / рабочий продукт"
    assert "Ранняя стадия" not in (view.progress_line or "")


def test_hard_caption_truncate_does_not_double_escape_ampersand(monkeypatch) -> None:
    """AUD-12: last-resort shrink must not turn &amp; into &amp;amp;."""
    import raundo.cards.startup_card as card_mod

    monkeypatch.setattr(card_mod, "_CAPTION_SOFT_MAX", 60)
    monkeypatch.setattr(card_mod, "_CAPTION_HARD_MAX", 48)
    view = build_startup_card_view(
        _startup(
            name="Name & Co",
            one_liner="Pitch with & symbols and more text to force shrink " * 3,
            intents=["investment", "pilots", "partners"],
            audience_codes=["b2c"],
            audience_note="a&b",
            progress_codes=["mvp"],
            monetization_status="planned",
            monetization_note="m&n",
            ask_amount_minor=1_500_000_000,
        ),
        sector_labels=["A&B", "C&D"],
        stage_label="MVP",
    )
    html = compact_startup_html(view)
    assert "&amp;amp;" not in html
    assert "&amp;" in html
    assert len(html) <= 48


def test_compact_caption_stays_under_telegram_limit() -> None:
    view = build_startup_card_view(
        _startup(
            one_liner="A" * 240,
            audience_codes=["b2c", "b2b_smb", "b2b_enterprise"],
            audience_note="семьи с пожилыми родственниками и клиники",
            progress_codes=["mvp", "pilots", "users"],
            monetization_status="planned",
            monetization_note="B" * 80,
            intents=["investment", "pilots", "partners", "users"],
            ask_amount_minor=25_000_000_00,
        ),
        sector_labels=["HealthTech", "AI/ML", "Consumer", "SaaS"],
        stage_label="MVP",
    )
    html = compact_startup_html(view)
    assert len(html) <= 1024
    assert not html.rstrip().endswith("<")
    assert html.count("<b>") == html.count("</b>")
