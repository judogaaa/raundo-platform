"""Telegram account binding and profile bootstrap."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.user import User
from raundo.repositories.users import UserRepository


class AccountService:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)

    async def bind_telegram_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        language_code: str | None,
    ) -> User:
        locale = (language_code or "ru")[:16]
        return await self._users.upsert_from_telegram(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            locale=locale,
        )

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self._users.get_by_telegram_id(telegram_id)
