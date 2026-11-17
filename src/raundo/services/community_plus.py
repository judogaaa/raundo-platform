"""Community PLUS: named collections and notification preferences.

Storage and entitlement rules are complete. Actual feeds/notifications arrive
with the discovery feed (Issue #3); until then these structures simply exist
and stay honest — nothing pretends to be a working feed.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.billing import CommunityNotificationPref, UserCollection, UserCollectionItem
from raundo.models.startup import Startup
from raundo.premium_catalog import PLAN_COMMUNITY_PLUS
from raundo.services.premium import PremiumService

FREE_NAMED_COLLECTIONS = 0  # free users keep the single default favorites list
MAX_NAMED_COLLECTIONS = 20


class CommunityPlusError(Exception):
    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


class CommunityPlusService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_default_collection(self, user_id: uuid.UUID) -> UserCollection:
        """Free-core single favorites list; safe to call on every action."""
        result = await self._session.scalars(
            select(UserCollection).where(
                UserCollection.user_id == user_id,
                UserCollection.is_default.is_(True),
            )
        )
        existing = result.first()
        if existing is not None:
            return existing
        collection = UserCollection(
            id=uuid.uuid4(),
            user_id=user_id,
            name="Избранное",
            is_default=True,
        )
        self._session.add(collection)
        await self._session.flush()
        return collection

    async def collections(self, user_id: uuid.UUID) -> list[UserCollection]:
        result = await self._session.scalars(
            select(UserCollection)
            .where(UserCollection.user_id == user_id)
            .order_by(UserCollection.is_default.desc(), UserCollection.name)
        )
        return list(result.all())

    async def create_collection(
        self, user_id: uuid.UUID, name: str
    ) -> UserCollection:
        """Extra named collections require an active Community PLUS."""
        clean = name.strip()
        if not (1 <= len(clean) <= 80):
            raise CommunityPlusError("Название коллекции: от 1 до 80 символов.", code="bad_name")
        service = PremiumService(self._session)
        if not await service.has_active_user_plan(user_id, PLAN_COMMUNITY_PLUS):
            raise CommunityPlusError(
                "Дополнительные коллекции доступны с Community PLUS.", code="premium_required"
            )
        existing = await self.collections(user_id)
        named = sum(1 for c in existing if not c.is_default)
        if named >= MAX_NAMED_COLLECTIONS:
            raise CommunityPlusError("Достигнут лимит коллекций.", code="limit")
        collection = UserCollection(
            id=uuid.uuid4(),
            user_id=user_id,
            name=clean,
            is_default=False,
        )
        self._session.add(collection)
        await self._session.flush()
        return collection

    async def add_item(
        self, collection: UserCollection, startup_id: uuid.UUID
    ) -> Optional[UserCollectionItem]:
        startup = await self._session.get(Startup, startup_id)
        if startup is None:
            raise CommunityPlusError("Стартап не найден.", code="not_found")
        exists = (
            await self._session.scalars(
                select(UserCollectionItem.id).where(
                    UserCollectionItem.collection_id == collection.id,
                    UserCollectionItem.startup_id == startup_id,
                )
            )
        ).first()
        if exists is not None:
            return None  # idempotent add
        max_pos = await self._session.scalar(
            select(func.coalesce(func.max(UserCollectionItem.position), 0)).where(
                UserCollectionItem.collection_id == collection.id
            )
        )
        item = UserCollectionItem(
            id=uuid.uuid4(),
            collection_id=collection.id,
            startup_id=startup_id,
            position=int(max_pos or 0) + 1,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def remove_item(
        self, user_id: uuid.UUID, collection_id: uuid.UUID, item_id: uuid.UUID
    ) -> bool:
        """Ownership is checked against the collection's user, not the request."""
        result = await self._session.execute(
            delete(UserCollectionItem)
            .where(
                UserCollectionItem.id == item_id,
                UserCollectionItem.collection_id == collection_id,
            )
            .where(
                UserCollectionItem.collection_id.in_(
                    select(UserCollection.id).where(UserCollection.user_id == user_id)
                )
            )
        )
        await self._session.flush()
        return bool(result.rowcount)

    async def notification_pref(self, user_id: uuid.UUID) -> CommunityNotificationPref:
        result = await self._session.scalars(
            select(CommunityNotificationPref).where(
                CommunityNotificationPref.user_id == user_id
            )
        )
        pref = result.first()
        if pref is not None:
            return pref
        pref = CommunityNotificationPref(id=uuid.uuid4(), user_id=user_id, sector_codes=[], enabled=True)
        self._session.add(pref)
        await self._session.flush()
        return pref
