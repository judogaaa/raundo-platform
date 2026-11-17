"""Foundation metadata, commands and migration revision presence."""

from pathlib import Path

import raundo.models  # noqa: F401
from raundo.bot_commands import PUBLIC_BOT_COMMANDS
from raundo.db.base import Base
from raundo.keyboards import CALLBACK_ACCEPT_CONSENT, consent_keyboard, main_menu


def test_expected_foundation_tables_registered() -> None:
    tables = set(Base.metadata.tables)
    assert {
        "users",
        "user_profiles",
        "user_consents",
        "sectors",
        "stages",
        "processed_updates",
        "outbox_events",
        "analytics_events",
        "startups",
        "startup_media",
        "startup_reviews",
        "investor_profiles",
        "investor_sector_preferences",
        "investor_stage_preferences",
        "investor_reviews",
    }.issubset(tables)


def test_public_bot_commands_include_core_slash_menu() -> None:
    names = {cmd.command for cmd in PUBLIC_BOT_COMMANDS}
    assert {
        "start",
        "rate",
        "startup",
        "investor",
        "top",
        "profile",
        "premium",
        "support",
        "terms",
        "privacy",
        "help",
        "cancel",
    }.issubset(names)
    # /admin and /version must NOT be advertised in the public command list.
    assert "admin" not in names
    assert "version" not in names


def test_consent_inline_callback_fits_telegram_limit() -> None:
    assert len(CALLBACK_ACCEPT_CONSENT.encode("utf-8")) <= 64
    markup = consent_keyboard()
    assert markup.inline_keyboard
    assert main_menu().is_persistent


def test_alembic_foundation_revision_file_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = root / "migrations" / "versions" / "20261022_0001_foundation.py"
    assert revision.is_file()
    text = revision.read_text(encoding="utf-8")
    assert 'revision: str = "20261022_0001"' in text
    assert "sectors" in text and "stages" in text
    profiles = root / "migrations" / "versions" / "20261022_0002_profiles.py"
    assert profiles.is_file()
    assert "20261022_0001" in profiles.read_text(encoding="utf-8")
