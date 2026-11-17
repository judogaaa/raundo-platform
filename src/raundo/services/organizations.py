"""Claim funding organizations without auto-verifying affiliation."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.domain import REVIEW_DRAFT, canonical_participant_type
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.participants import DIRECT_INVEST_SUPPORT_MODES, default_support_modes_for_type

MEMBERSHIP_CLAIMED = "claimed"
MEMBERSHIP_UNDER_REVIEW = "under_review"
MEMBERSHIP_APPROVED = "approved"
MEMBERSHIP_REVOKED = "revoked"


class OrganizationServiceError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def normalize_org_name(name: str) -> str:
    text = re.sub(r"\s+", " ", name.strip().lower())
    return text[:256]


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_organization(
        self,
        *,
        user_id: uuid.UUID,
        name: str,
        participant_type: str,
        role: str | None = None,
        support_modes: list[str] | None = None,
        claims_direct_investment: bool = False,
    ) -> tuple[FundingOrganization, OrganizationMembership]:
        """
        Create or reuse a draft organization and a claimed membership.

        Never auto-approves. Approved memberships cannot self-escalate role/mandate
        while remaining approved. Revoked memberships cannot be restored by the user.
        """
        canon = canonical_participant_type(participant_type) or participant_type
        modes = support_modes or default_support_modes_for_type(canon)
        normalized = normalize_org_name(name)
        existing = await self._session.execute(
            select(FundingOrganization).where(
                FundingOrganization.normalized_name == normalized,
                FundingOrganization.participant_type == canon,
            )
        )
        org = existing.scalar_one_or_none()
        if org is None:
            from raundo.services.profile_limits import KIND_ORGANIZATION, ensure_can_create

            await ensure_can_create(
                self._session, user_id=user_id, kind=KIND_ORGANIZATION
            )
            org = FundingOrganization(
                id=uuid.uuid4(),
                name=name.strip()[:256],
                normalized_name=normalized,
                participant_type=canon,
                support_modes=list(modes),
                open_for_requests=True,
                review_status=REVIEW_DRAFT,
                created_by_user_id=user_id,
            )
            self._session.add(org)
            await self._session.flush()

        mem_result = await self._session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org.id,
                OrganizationMembership.user_id == user_id,
            )
        )
        membership = mem_result.scalar_one_or_none()
        mandate = claims_direct_investment or bool(
            set(modes) & DIRECT_INVEST_SUPPORT_MODES
        )

        if membership is None:
            membership = OrganizationMembership(
                id=uuid.uuid4(),
                organization_id=org.id,
                user_id=user_id,
                role=role,
                status=MEMBERSHIP_CLAIMED,
                claims_direct_investment_mandate=mandate,
            )
            self._session.add(membership)
            await self._session.flush()
            return org, membership

        if membership.status == MEMBERSHIP_REVOKED:
            raise OrganizationServiceError(
                "Принадлежность к этой организации отозвана администратором. "
                "Самостоятельно восстановить её нельзя. Обратитесь в поддержку."
            )

        if membership.status == MEMBERSHIP_APPROVED:
            self._apply_change_to_approved_membership(
                membership, role=role, mandate=mandate
            )
        else:
            if role is not None:
                membership.role = role
            membership.claims_direct_investment_mandate = mandate

        await self._session.flush()
        return org, membership

    def _apply_change_to_approved_membership(
        self,
        membership: OrganizationMembership,
        *,
        role: str | None,
        mandate: bool,
    ) -> None:
        """Approved rights are immutable without re-review."""
        role_changed = role is not None and role != membership.role
        mandate_escalation = mandate and not membership.claims_direct_investment_mandate
        mandate_changed = mandate != membership.claims_direct_investment_mandate

        if not role_changed and not mandate_changed:
            return

        membership.status = MEMBERSHIP_UNDER_REVIEW
        membership.verified_at = None
        membership.verified_by = None
        membership.decision_reason = (
            "self_service_change_requires_review:"
            f"role_changed={role_changed};mandate_escalation={mandate_escalation}"
        )
        if role is not None:
            membership.role = role
        membership.claims_direct_investment_mandate = mandate

    async def sync_membership_role(
        self,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        role: str | None,
        claims_direct_investment: bool | None = None,
    ) -> OrganizationMembership:
        """Keep organization_memberships.role in sync with investor profile.position."""
        result = await self._session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise OrganizationServiceError("Сначала укажите организацию.")

        if membership.status == MEMBERSHIP_REVOKED:
            raise OrganizationServiceError(
                "Принадлежность отозвана администратором и не может быть изменена."
            )

        mandate = (
            membership.claims_direct_investment_mandate
            if claims_direct_investment is None
            else claims_direct_investment
        )

        if membership.status == MEMBERSHIP_APPROVED:
            self._apply_change_to_approved_membership(
                membership, role=role, mandate=mandate
            )
        else:
            membership.role = role
            if claims_direct_investment is not None:
                membership.claims_direct_investment_mandate = claims_direct_investment

        await self._session.flush()
        return membership
