"""Organization PRO: seats, team workspace, org-owned data.

Security model (Issue #5 + FUNDING_PARTICIPANTS):
- The subscription belongs to the organization, never to an arbitrary employee.
- Seats are granted only to members with an *approved* membership; a claimed
  name or under-review affiliation grants nothing.
- Revoking a membership (or the seat) immediately blocks access to team data —
  every read re-checks seat + membership + subscription server-side.
- Expiry never deletes team data; it only blocks premium actions.
- No self-escalation: only an existing active seat holder can grant seats.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.domain import MAX_ORG_SEATS
from raundo.models.billing import (
    OrgCollection,
    OrgCollectionItem,
    OrgStartupAssignment,
    PremiumOrgSeat,
    TeamPrivateComment,
)
from raundo.models.organization import OrganizationMembership
from raundo.models.startup import Startup
from raundo.premium_catalog import PLAN_ORGANIZATION_PRO
from raundo.services.premium import MEMBERSHIP_APPROVED, PremiumService

ASSIGNMENT_OPEN = "open"
ASSIGNMENT_IN_PROGRESS = "in_progress"
ASSIGNMENT_DONE = "done"
ASSIGNMENT_DISMISSED = "dismissed"

ASSIGNMENT_STATUSES = (
    ASSIGNMENT_OPEN,
    ASSIGNMENT_IN_PROGRESS,
    ASSIGNMENT_DONE,
    ASSIGNMENT_DISMISSED,
)


class OrgProError(Exception):
    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


class OrgProService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------------------------------------------------------------- access

    async def active_subscription(
        self, organization_id: uuid.UUID
    ) -> Optional[object]:
        return await PremiumService(self._session).active_subscription(
            PLAN_ORGANIZATION_PRO, organization_id=organization_id
        )

    async def active_seat(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[PremiumOrgSeat]:
        """
        Full server-side access check: active org subscription + active seat +
        approved membership. Any of the three failing means no access.
        """
        sub = await self.active_subscription(organization_id)
        if sub is None:
            return None
        seat = (
            await self._session.scalars(
                select(PremiumOrgSeat).where(
                    PremiumOrgSeat.organization_id == organization_id,
                    PremiumOrgSeat.user_id == user_id,
                    PremiumOrgSeat.status == "active",
                )
            )
        ).first()
        if seat is None:
            return None
        if not await PremiumService(self._session).is_approved_member(
            organization_id, user_id
        ):
            return None
        return seat

    # ----------------------------------------------------------------- seats

    async def seats(self, organization_id: uuid.UUID) -> list[PremiumOrgSeat]:
        result = await self._session.scalars(
            select(PremiumOrgSeat)
            .where(PremiumOrgSeat.organization_id == organization_id)
            .order_by(PremiumOrgSeat.granted_at)
        )
        return list(result.all())

    async def grant_seat(
        self,
        *,
        organization_id: uuid.UUID,
        target_user_id: uuid.UUID,
        granted_by_user_id: uuid.UUID,
    ) -> tuple[PremiumOrgSeat, bool]:
        """
        Add one team seat (max 5 active). Requirements:
        - actor already holds an active seat (no self-escalation from nothing);
        - the org has an active Organization PRO subscription;
        - the target has an approved membership in THIS organization.
        """
        if await self.active_seat(organization_id, granted_by_user_id) is None:
            raise OrgProError(
                "Места назначает держатель активного места организации.",
                code="not_seat_holder",
            )
        if await self.active_subscription(organization_id) is None:
            raise OrgProError("Требуется активный Organization PRO.", code="premium_required")
        if not await PremiumService(self._session).is_approved_member(
            organization_id, target_user_id
        ):
            raise OrgProError(
                "Место можно выдать только подтверждённому представителю организации.",
                code="membership_not_approved",
            )
        existing = (
            await self._session.scalars(
                select(PremiumOrgSeat).where(
                    PremiumOrgSeat.organization_id == organization_id,
                    PremiumOrgSeat.user_id == target_user_id,
                )
            )
        ).first()
        if existing is not None:
            if existing.status == "active":
                return existing, False
            existing.status = "active"
            existing.revoked_at = None
            existing.revoke_reason = None
            await self._session.flush()
            return existing, True
        active_count = await self._session.scalar(
            select(func.count(PremiumOrgSeat.id)).where(
                PremiumOrgSeat.organization_id == organization_id,
                PremiumOrgSeat.status == "active",
            )
        )
        if int(active_count or 0) >= MAX_ORG_SEATS:
            raise OrgProError(
                f"Максимум {MAX_ORG_SEATS} активных мест.", code="seat_limit"
            )
        active_sub = await self.active_subscription(organization_id)
        assert active_sub is not None  # checked above; narrows the type
        seat = PremiumOrgSeat(
            id=uuid.uuid4(),
            subscription_id=active_sub.id,
            organization_id=organization_id,
            user_id=target_user_id,
            granted_by_user_id=granted_by_user_id,
        )
        self._session.add(seat)
        await self._session.flush()
        return seat, True

    async def revoke_seat(
        self,
        *,
        organization_id: uuid.UUID,
        target_user_id: uuid.UUID,
        revoked_by_user_id: uuid.UUID,
        reason: str | None = None,
    ) -> bool:
        if await self.active_seat(organization_id, revoked_by_user_id) is None:
            raise OrgProError("Отзывать места может держатель активного места.", code="not_seat_holder")
        seat = (
            await self._session.scalars(
                select(PremiumOrgSeat).where(
                    PremiumOrgSeat.organization_id == organization_id,
                    PremiumOrgSeat.user_id == target_user_id,
                    PremiumOrgSeat.status == "active",
                )
            )
        ).first()
        if seat is None:
            return False
        seat.status = "revoked"
        seat.revoked_at = datetime.now(timezone.utc)
        seat.revoke_reason = reason
        await self._session.flush()
        return True

    # -------------------------------------------------------- team comments

    async def team_comments(
        self, organization_id: uuid.UUID, reader_user_id: uuid.UUID, startup_id: uuid.UUID | None = None
    ) -> list[TeamPrivateComment]:
        """Reader must hold an active seat right now; org-scoped only."""
        if await self.active_seat(organization_id, reader_user_id) is None:
            raise OrgProError("Нет активного места в этой организации.", code="no_access")
        stmt = (
            select(TeamPrivateComment)
            .where(TeamPrivateComment.organization_id == organization_id)
            .order_by(TeamPrivateComment.created_at)
        )
        if startup_id is not None:
            stmt = stmt.where(TeamPrivateComment.startup_id == startup_id)
        return list((await self._session.scalars(stmt)).all())

    async def add_team_comment(
        self,
        *,
        organization_id: uuid.UUID,
        author_user_id: uuid.UUID,
        body: str,
        startup_id: uuid.UUID | None = None,
    ) -> TeamPrivateComment:
        if await self.active_seat(organization_id, author_user_id) is None:
            raise OrgProError("Нет активного места в этой организации.", code="no_access")
        clean = body.strip()
        if not clean:
            raise OrgProError("Пустой комментарий.", code="bad_comment")
        if startup_id is not None:
            if await self._session.get(Startup, startup_id) is None:
                raise OrgProError("Стартап не найден.", code="not_found")
        comment = TeamPrivateComment(
            id=uuid.uuid4(),
            organization_id=organization_id,
            startup_id=startup_id,
            author_user_id=author_user_id,
            body=clean,
        )
        self._session.add(comment)
        await self._session.flush()
        return comment

    # ----------------------------------------------------------- collections

    async def org_collections(self, organization_id: uuid.UUID) -> list[OrgCollection]:
        result = await self._session.scalars(
            select(OrgCollection)
            .where(OrgCollection.organization_id == organization_id)
            .order_by(OrgCollection.name)
        )
        return list(result.all())

    async def create_org_collection(
        self, *, organization_id: uuid.UUID, actor_user_id: uuid.UUID, name: str
    ) -> OrgCollection:
        if await self.active_seat(organization_id, actor_user_id) is None:
            raise OrgProError("Нет активного места в этой организации.", code="no_access")
        clean = name.strip()
        if not (1 <= len(clean) <= 80):
            raise OrgProError("Название подборки: от 1 до 80 символов.", code="bad_name")
        collection = OrgCollection(
            id=uuid.uuid4(),
            organization_id=organization_id,
            name=clean,
            created_by_user_id=actor_user_id,
        )
        self._session.add(collection)
        await self._session.flush()
        return collection

    async def add_to_org_collection(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        collection_id: uuid.UUID,
        startup_id: uuid.UUID,
    ) -> Optional[OrgCollectionItem]:
        if await self.active_seat(organization_id, actor_user_id) is None:
            raise OrgProError("Нет активного места в этой организации.", code="no_access")
        collection = await self._session.get(OrgCollection, collection_id)
        if collection is None or collection.organization_id != organization_id:
            raise OrgProError("Подборка другой организации.", code="cross_org")
        startup = await self._session.get(Startup, startup_id)
        if startup is None:
            raise OrgProError("Стартап не найден.", code="not_found")
        exists = (
            await self._session.scalars(
                select(OrgCollectionItem.id).where(
                    OrgCollectionItem.collection_id == collection_id,
                    OrgCollectionItem.startup_id == startup_id,
                )
            )
        ).first()
        if exists is not None:
            return None
        item = OrgCollectionItem(
            id=uuid.uuid4(),
            collection_id=collection_id,
            startup_id=startup_id,
            added_by_user_id=actor_user_id,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    # ----------------------------------------------------------- assignments

    async def assignments(self, organization_id: uuid.UUID) -> list[OrgStartupAssignment]:
        result = await self._session.scalars(
            select(OrgStartupAssignment)
            .where(OrgStartupAssignment.organization_id == organization_id)
            .order_by(OrgStartupAssignment.created_at)
        )
        return list(result.all())

    async def assign_startup(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        startup_id: uuid.UUID,
        assigned_to_user_id: uuid.UUID,
        note: str | None = None,
    ) -> OrgStartupAssignment:
        """Assignments only to current seat holders of the same organization."""
        if await self.active_seat(organization_id, actor_user_id) is None:
            raise OrgProError("Нет активного места в этой организации.", code="no_access")
        if await self.active_seat(organization_id, assigned_to_user_id) is None:
            raise OrgProError(
                "Назначать можно только держателям активных мест этой организации.",
                code="assignee_no_seat",
            )
        startup = await self._session.get(Startup, startup_id)
        if startup is None:
            raise OrgProError("Стартап не найден.", code="not_found")
        existing = (
            await self._session.scalars(
                select(OrgStartupAssignment).where(
                    OrgStartupAssignment.organization_id == organization_id,
                    OrgStartupAssignment.startup_id == startup_id,
                )
            )
        ).first()
        if existing is not None:
            existing.assigned_to_user_id = assigned_to_user_id
            if note is not None:
                existing.note = note[:240]
            await self._session.flush()
            return existing
        assignment = OrgStartupAssignment(
            id=uuid.uuid4(),
            organization_id=organization_id,
            startup_id=startup_id,
            assigned_to_user_id=assigned_to_user_id,
            assigned_by_user_id=actor_user_id,
            status=ASSIGNMENT_OPEN,
            note=note[:240] if note is not None else None,
        )
        self._session.add(assignment)
        await self._session.flush()
        return assignment

    # ------------------------------------------------- membership revocation

    async def revoke_seat_for_membership_change(
        self, organization_id: uuid.UUID, user_id: uuid.UUID, reason: str
    ) -> int:
        """
        Called when a membership leaves 'approved' (admin or member action):
        the seat is deactivated immediately; team data is kept, access is gone.
        """
        seats = (
            await self._session.scalars(
                select(PremiumOrgSeat).where(
                    PremiumOrgSeat.organization_id == organization_id,
                    PremiumOrgSeat.user_id == user_id,
                    PremiumOrgSeat.status == "active",
                )
            )
        ).all()
        for seat in seats:
            seat.status = "revoked"
            seat.revoked_at = datetime.now(timezone.utc)
            seat.revoke_reason = reason
        if seats:
            await self._session.flush()
        return len(seats)


def membership_is_approved(membership: OrganizationMembership) -> bool:
    return membership.status == MEMBERSHIP_APPROVED
