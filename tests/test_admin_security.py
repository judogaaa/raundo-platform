"""Admin MFA helpers and RBAC guards."""

from __future__ import annotations

import uuid

import pyotp
import pytest
from fastapi import HTTPException

from raundo.admin_api.app import ROLES_OWNER, require_roles
from raundo.admin_api.security import hash_password
from raundo.models.admin_panel import AdminAccount
from raundo.services.admin_auth import (
    generate_backup_codes,
    generate_totp_secret,
    verify_mfa_code,
    verify_totp,
)


def test_require_roles_blocks_moderator_from_owner_actions() -> None:
    account = AdminAccount(
        id=uuid.uuid4(),
        login="mod",
        password_hash="x",
        role="moderator",
    )
    with pytest.raises(HTTPException) as exc:
        require_roles(account, ROLES_OWNER)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_totp_and_backup_code_flow(session) -> None:
    secret = generate_totp_secret()
    codes, stored = generate_backup_codes(3)
    account = AdminAccount(
        id=uuid.uuid4(),
        login="mfa_test",
        password_hash=hash_password("longpassword1"),
        role="owner",
        totp_secret=secret,
        backup_codes_hash=stored,
        must_change_password=False,
    )
    session.add(account)
    await session.flush()
    assert verify_totp(account, pyotp.TOTP(secret).now())
    backup = codes[0]
    assert verify_mfa_code(account, backup)
    assert not verify_mfa_code(account, backup)
