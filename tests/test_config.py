"""Startup permission bootstrap should not rely on mutable Telegram usernames."""

from raundo.config import Settings


def test_admin_ids_are_strict_numeric() -> None:
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="token_not_used_for_network",
        ADMIN_TELEGRAM_IDS=" 1001, 2002 , @fake, junk, ",
    )
    assert settings.is_admin(1001)
    assert settings.is_admin(2002)
    assert not settings.is_admin(0)
    assert not settings.is_admin(1003)
    assert not settings.is_admin(None)


def test_missing_token_fails_fast() -> None:
    settings = Settings(_env_file=None, BOT_TOKEN="")
    try:
        settings.require_valid_runtime()
    except RuntimeError as error:
        assert "BOT_TOKEN" in str(error)
    else:
        raise AssertionError("Missing token must not be accepted")


def test_policy_version_defaults_to_draft() -> None:
    settings = Settings(_env_file=None, BOT_TOKEN="x")
    assert settings.policy_version
    assert "draft" in settings.policy_version or settings.policy_version.startswith("20")


def test_sql_echo_defaults_off() -> None:
    settings = Settings(_env_file=None, BOT_TOKEN="x", ENVIRONMENT="development")
    assert settings.sql_echo is False


def test_telegram_test_mode_defaults_off() -> None:
    assert Settings(_env_file=None).telegram_test_mode is False


def test_telegram_test_mode_is_refused_in_production() -> None:
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        ENVIRONMENT="production",
        TELEGRAM_TEST_MODE="true",
    )
    try:
        settings.require_valid_runtime()
    except RuntimeError as error:
        assert "TELEGRAM_TEST_MODE" in str(error)
    else:
        raise AssertionError("test environment must never run as production")


def test_telegram_test_mode_allows_test_stars() -> None:
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        ENVIRONMENT="staging",
        TELEGRAM_TEST_MODE="true",
        PAYMENTS_MODE="demo",
        PAYMENTS_STARS_ENABLED="true",
    )
    settings.require_valid_runtime()
    assert settings.telegram_test_mode is True


def test_stars_outside_test_environment_must_be_live() -> None:
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        ENVIRONMENT="development",
        PAYMENTS_MODE="demo",
        PAYMENTS_STARS_ENABLED="true",
    )
    try:
        settings.require_valid_runtime()
    except RuntimeError as error:
        assert "TELEGRAM_TEST_MODE" in str(error)
    else:
        raise AssertionError("demo Stars without test mode would charge real Stars")


def test_live_stars_require_approved_prices_and_legal_ready() -> None:
    incomplete = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        ENVIRONMENT="development",
        PAYMENTS_MODE="live",
        PAYMENTS_STARS_ENABLED="true",
        PAYMENTS_STARS_PRICES_APPROVED="true",
        PAYMENTS_LEGAL_READY="false",
    )
    try:
        incomplete.require_valid_runtime()
    except RuntimeError as error:
        assert "PAYMENTS_LEGAL_READY" in str(error)
    else:
        raise AssertionError("live Stars without legal readiness must be refused")

    complete = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        ENVIRONMENT="development",
        PAYMENTS_MODE="live",
        PAYMENTS_STARS_ENABLED="true",
        PAYMENTS_STARS_PRICES_APPROVED="true",
        PAYMENTS_LEGAL_READY="true",
    )
    complete.require_valid_runtime()


def test_runtime_version_report_has_no_secrets() -> None:
    from raundo.runtime_info import (
        APP_NAME,
        _repo_root_from_package,
        format_version_report,
        resolve_git_sha,
    )

    text = format_version_report()
    assert APP_NAME in text
    assert "PID:" in text
    assert "Git SHA:" in text
    assert "BOT_TOKEN" not in text
    assert "DATABASE_URL" not in text
    assert "redis://" not in text.lower()
    assert "password" not in text.lower()
    root = _repo_root_from_package()
    assert root is not None
    assert (root / "pyproject.toml").exists()
    resolve_git_sha.cache_clear()
    sha = resolve_git_sha()
    assert sha is not None
    assert len(sha) >= 40


def test_yookassa_key_is_read_from_the_secret_file(tmp_path) -> None:  # noqa: ANN001
    secret = tmp_path / "yookassa.key"
    secret.write_text("live_file_secret\n", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        YOOKASSA_SHOP_ID="1517068",
        YOOKASSA_SECRET_KEY="",
        YOOKASSA_SECRET_KEY_FILE=str(secret),
    )
    assert settings.yookassa_key() == "live_file_secret"
    assert settings.yookassa_configured() is True
    # The value is never stored on the settings object or its repr.
    assert "live_file_secret" not in repr(settings)
    assert str(secret) in settings.yookassa_secret_key_file


def test_env_yookassa_key_wins_over_the_file(tmp_path) -> None:  # noqa: ANN001
    secret = tmp_path / "yookassa.key"
    secret.write_text("file_secret", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        YOOKASSA_SECRET_KEY="env_secret",
        YOOKASSA_SECRET_KEY_FILE=str(secret),
    )
    assert settings.yookassa_key() == "env_secret"


def test_missing_yookassa_secret_file_is_not_configured(tmp_path) -> None:  # noqa: ANN001
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        YOOKASSA_SHOP_ID="1517068",
        YOOKASSA_SECRET_KEY="",
        YOOKASSA_SECRET_KEY_FILE=str(tmp_path / "absent.key"),
    )
    assert settings.yookassa_key() == ""
    assert settings.yookassa_configured() is False


def test_provider_available_uses_the_mounted_key(tmp_path) -> None:  # noqa: ANN001
    from raundo.services.payments import provider_available

    secret = tmp_path / "yookassa.key"
    secret.write_text("live_file_secret", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        PAYMENTS_MODE="provider_test",
        YOOKASSA_SHOP_ID="1517068",
        YOOKASSA_SECRET_KEY="",
        YOOKASSA_SECRET_KEY_FILE=str(secret),
    )
    assert provider_available(settings) is True

    no_key = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        PAYMENTS_MODE="provider_test",
        YOOKASSA_SHOP_ID="1517068",
        YOOKASSA_SECRET_KEY="",
        YOOKASSA_SECRET_KEY_FILE=str(tmp_path / "absent.key"),
    )
    assert provider_available(no_key) is False
