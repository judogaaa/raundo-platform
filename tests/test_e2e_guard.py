"""Regression tests for the E2E/live database isolation guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from raundo.testing.e2e_guard import (
    E2EGuardError,
    assert_e2e_actual_db,
    assert_e2e_db_url,
    db_name_from_url,
)

DEV = "postgresql+asyncpg://raundo:pw@127.0.0.1:55432/raundo_admin_dev"


def test_dev_db_is_allowed() -> None:
    assert assert_e2e_db_url(DEV) == "raundo_admin_dev"


def test_live_db_is_refused() -> None:
    live = "postgresql+asyncpg://raundo:pw@127.0.0.1:55432/raundo"
    with pytest.raises(E2EGuardError):
        assert_e2e_db_url(live)
    with pytest.raises(E2EGuardError):
        assert_e2e_actual_db("raundo")


def test_other_databases_are_refused() -> None:
    for url in (
        "postgresql+asyncpg://raundo:pw@127.0.0.1:55432/raundo_local_suite",
        "postgresql+asyncpg://raundo:pw@127.0.0.1:55432/postgres",
        "postgresql+asyncpg://raundo:pw@127.0.0.1:55432/raundo_admin_dev_extra",
    ):
        with pytest.raises(E2EGuardError):
            assert_e2e_db_url(url)


def test_url_parser_handles_driver_and_query() -> None:
    assert db_name_from_url(DEV) == "raundo_admin_dev"
    assert (
        db_name_from_url(
            "postgresql+asyncpg://raundo:pw@127.0.0.1:55432/raundo_admin_dev?ssl=disable"
        )
        == "raundo_admin_dev"
    )


def test_actual_db_must_be_dev() -> None:
    assert assert_e2e_actual_db("raundo_admin_dev") == "raundo_admin_dev"
    with pytest.raises(E2EGuardError):
        assert_e2e_actual_db("postgres")


def test_playwright_config_is_isolated() -> None:
    config = Path(__file__).resolve().parents[1] / "admin-web" / "playwright.config.ts"
    text = config.read_text(encoding="utf-8")
    assert "E2E_API_PORT = 8091" in text
    assert "E2E_WEB_PORT = 3100" in text
    assert "reuseExistingServer: false" in text
    assert 'e2eDbName === "raundo"' in text
