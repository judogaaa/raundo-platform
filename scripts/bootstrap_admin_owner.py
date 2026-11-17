"""Create the first Owner if none exists. Prints the one-time password once to stdout."""

from __future__ import annotations

import asyncio
import secrets
import sys
import uuid

from sqlalchemy import func, select

from raundo.admin_api.security import hash_password
from raundo.db.session import get_session_factory
from raundo.models.admin_panel import AdminAccount


async def main() -> int:
    login = sys.argv[1] if len(sys.argv) > 1 else "owner"
    factory = get_session_factory()
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(AdminAccount))
        if count:
            print("owner_exists")
            return 0
        password = secrets.token_urlsafe(18)
        session.add(
            AdminAccount(
                id=uuid.uuid4(),
                login=login,
                password_hash=hash_password(password),
                role="owner",
            )
        )
        await session.commit()
        print(f"login={login}")
        print(f"password={password}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
