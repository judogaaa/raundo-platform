"""MFA (TOTP + one-time backup codes) and password lifecycle."""

from __future__ import annotations

import json
import uuid

import pyotp
import pytest

from raundo.models.admin_panel import AdminAccount
from raundo.services.admin_auth import (
    change_password,
    consume_backup_code,
    generate_backup_codes,
    generate_totp_secret,
    verify_mfa_code,
    verify_totp,
)


def _account(secret: str | None = None) -> AdminAccount:
    return AdminAccount(
        id=uuid.uuid4(),
        login="owner",
        password_hash="x",
        role="owner",
        totp_secret=secret,
        mfa_enabled=bool(secret),
        must_change_password=True,
    )


def test_totp_code_accepted_and_wrong_code_rejected() -> None:
    secret = generate_totp_secret()
    account = _account(secret)
    assert verify_totp(account, pyotp.TOTP(secret).now()) is True
    assert verify_totp(account, "000000") is False


def test_totp_requires_secret() -> None:
    assert verify_totp(_account(None), "123456") is False


def test_backup_code_is_single_use() -> None:
    codes, digest = generate_backup_codes()
    account = _account(generate_totp_secret())
    account.backup_codes_hash = digest
    assert consume_backup_code(account, codes[0]) is True
    assert consume_backup_code(account, codes[0]) is False
    remaining = json.loads(account.backup_codes_hash or "[]")
    assert len(remaining) == len(codes) - 1
    assert consume_backup_code(account, codes[1]) is True


def test_last_backup_code_clears_hash() -> None:
    codes, digest = generate_backup_codes(count=1)
    account = _account(generate_totp_secret())
    account.backup_codes_hash = digest
    assert consume_backup_code(account, codes[0]) is True
    assert account.backup_codes_hash is None


def test_backup_code_is_case_and_space_tolerant() -> None:
    codes, digest = generate_backup_codes(count=2)
    account = _account(generate_totp_secret())
    account.backup_codes_hash = digest
    assert consume_backup_code(account, f"  {codes[0]}  ") is True


def test_verify_mfa_code_accepts_totp_or_backup_code() -> None:
    secret = generate_totp_secret()
    codes, digest = generate_backup_codes(count=2)
    account = _account(secret)
    account.backup_codes_hash = digest
    assert verify_mfa_code(account, pyotp.TOTP(secret).now()) is True
    assert verify_mfa_code(account, codes[0]) is True
    assert verify_mfa_code(account, "zzzz") is False


def test_password_change_clears_temporary_flag() -> None:
    account = _account(None)
    account.must_change_password = True
    with pytest.raises(ValueError):
        change_password(account, "short")
    change_password(account, "long-enough-password")
    assert account.must_change_password is False
    assert account.password_hash != "x"
