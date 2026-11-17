"""Ensure dedicated E2E staff account. Does not modify the human dev Owner login."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from pathlib import Path

from sqlalchemy import select

from raundo.admin_api.security import hash_password
from raundo.db.session import get_session_factory
from raundo.models.admin_panel import AdminAccount

E2E_LOGIN = "e2e_owner"
E2E_SUPPORT_LOGIN = "e2e_support"
E2E_MFA_LOGIN = "e2e_mfa"


def _write_credentials(password: str, support_password: str, mfa_password: str) -> None:
    creds = Path(__file__).resolve().parents[1] / "admin-web" / ".e2e-credentials.json"
    creds.write_text(
        json.dumps(
            {
                "login": E2E_LOGIN,
                "password": password,
                "supportLogin": E2E_SUPPORT_LOGIN,
                "supportPassword": support_password,
                "mfaLogin": E2E_MFA_LOGIN,
                "mfaPassword": mfa_password,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


async def _ensure_account(session, login: str, password: str, role: str):
    row = await session.execute(select(AdminAccount).where(AdminAccount.login == login))
    account = row.scalar_one_or_none()
    if account is None:
        account = AdminAccount(
            id=uuid.uuid4(),
            login=login,
            password_hash=hash_password(password),
            role=role,
            must_change_password=False,
            mfa_enabled=False,
        )
        session.add(account)
    else:
        if os.environ.get("E2E_ADMIN_PASSWORD"):
            account.password_hash = hash_password(password)
        account.role = role
        account.must_change_password = False
        account.mfa_enabled = False
        account.disabled_at = None
    return account


async def main() -> int:
    factory = get_session_factory()
    password = os.environ.get("E2E_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
    support_password = (
        os.environ.get("E2E_SUPPORT_PASSWORD") or secrets.token_urlsafe(18)
    )
    mfa_password = os.environ.get("E2E_MFA_PASSWORD") or secrets.token_urlsafe(18)
    async with factory() as session:
        await _ensure_account(session, E2E_LOGIN, password, "owner")
        await _ensure_account(session, E2E_SUPPORT_LOGIN, support_password, "support")
        mfa_account = await _ensure_account(session, E2E_MFA_LOGIN, mfa_password, "support")
        # A reusable E2E account must never stay enrolled in TOTP.
        mfa_account.mfa_enabled = False
        mfa_account.totp_secret = None
        mfa_account.backup_codes_hash = None
        await session.commit()
    creds = Path(__file__).resolve().parents[1] / "admin-web" / ".e2e-credentials.json"
    if not os.environ.get("E2E_ADMIN_PASSWORD") and creds.exists():
        print("e2e_exists_credentials_unchanged")
        return 0
    _write_credentials(password, support_password, mfa_password)
    print(f"e2e_credentials_written={creds.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
