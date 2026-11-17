"""User persistence operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from raundo.models.user import User, UserConsent, UserProfile


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        stmt = (
            select(User)
            .where(User.telegram_id == telegram_id)
            .options(selectinload(User.consents), selectinload(User.profile))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_from_telegram(
        self,
        *,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        locale: str = "ru",
    ) -> User:
        user = await self.get_by_telegram_id(telegram_id)
        now = datetime.now(timezone.utc)
        if user is None:
            user = User(
                id=uuid.uuid4(),
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                locale=locale,
                status="active",
                last_seen_at=now,
            )
            user.profile = UserProfile(user_id=user.id, display_name=first_name)
            self._session.add(user)
        else:
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.last_seen_at = now
            if locale:
                user.locale = locale
            if user.profile is None:
                user.profile = UserProfile(user_id=user.id, display_name=first_name)
        await self._session.flush()
        return user

    async def has_active_consent(
        self,
        user_id: uuid.UUID,
        *,
        kind: str,
        policy_version: str,
    ) -> bool:
        stmt = select(UserConsent).where(
            UserConsent.user_id == user_id,
            UserConsent.kind == kind,
            UserConsent.policy_version == policy_version,
            UserConsent.revoked_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def grant_consent(
        self,
        user_id: uuid.UUID,
        *,
        kind: str,
        policy_version: str,
    ) -> UserConsent:
        existing = await self._session.execute(
            select(UserConsent).where(
                UserConsent.user_id == user_id,
                UserConsent.kind == kind,
                UserConsent.policy_version == policy_version,
            )
        )
        consent = existing.scalar_one_or_none()
        if consent is None:
            consent = UserConsent(
                id=uuid.uuid4(),
                user_id=user_id,
                kind=kind,
                policy_version=policy_version,
            )
            self._session.add(consent)
        else:
            consent.revoked_at = None
            consent.granted_at = datetime.now(timezone.utc)
        await self._session.flush()
        return consent
