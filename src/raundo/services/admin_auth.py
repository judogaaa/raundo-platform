"""MFA and password lifecycle for RAUNDO Admin staff accounts."""

from __future__ import annotations

import hashlib
import json
import secrets

import pyotp

from raundo.admin_api.security import hash_password
from raundo.models.admin_panel import AdminAccount


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(account: AdminAccount, code: str) -> bool:
    if not account.totp_secret:
        return False
    totp = pyotp.TOTP(account.totp_secret)
    return totp.verify(code.strip(), valid_window=1)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def generate_backup_codes(count: int = 8) -> tuple[list[str], str]:
    codes = [secrets.token_hex(4) for _ in range(count)]
    stored = json.dumps([_hash_code(c) for c in codes])
    return codes, stored


def consume_backup_code(account: AdminAccount, code: str) -> bool:
    if not account.backup_codes_hash:
        return False
    try:
        hashes: list[str] = json.loads(account.backup_codes_hash)
    except json.JSONDecodeError:
        return False
    digest = _hash_code(code)
    if digest not in hashes:
        return False
    hashes.remove(digest)
    account.backup_codes_hash = json.dumps(hashes) if hashes else None
    return True


def verify_mfa_code(account: AdminAccount, code: str) -> bool:
    if verify_totp(account, code):
        return True
    return consume_backup_code(account, code)


def change_password(account: AdminAccount, new_password: str) -> None:
    if len(new_password) < 10:
        raise ValueError("password_too_short")
    account.password_hash = hash_password(new_password)
    account.must_change_password = False
