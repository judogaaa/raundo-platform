"""Investor profile draft lifecycle and preference persistence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from raundo.domain import (
    REVIEW_APPROVED,
    REVIEW_DRAFT,
    REVIEW_PENDING_CHANGES,
    REVIEW_SUBMITTED,
    can_edit_review_state,
    can_submit_review_state,
    cheque_is_relevant,
    investor_needs_organization,
    validate_cheque_bounds,
    validate_investor_preferences,
)
from raundo.models.investor import (
    InvestorProfile,
    InvestorReview,
    InvestorSectorPreference,
    InvestorStagePreference,
)
from raundo.participants import DIRECT_INVEST_SUPPORT_MODES
from raundo.repositories.catalog import CatalogRepository
from raundo.services.profile_lifecycle import (
    begin_investor_approved_edit,
    register_investor_owner_change,
    soft_delete_investor,
    withdraw_investor_sensitive_public,
)
from raundo.ui_labels import not_editable_message


class InvestorServiceError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class InvestorService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._catalog = CatalogRepository(session)

    async def list_for_user(self, user_id: uuid.UUID) -> list[InvestorProfile]:
        result = await self._session.execute(
            select(InvestorProfile)
            .where(InvestorProfile.user_id == user_id, InvestorProfile.deleted_at.is_(None))
            .order_by(InvestorProfile.created_at, InvestorProfile.id)
            .options(
                selectinload(InvestorProfile.sector_preferences),
                selectinload(InvestorProfile.stage_preferences),
                selectinload(InvestorProfile.reviews),
            )
        )
        return list(result.scalars().all())

    async def list_archived(self, user_id: uuid.UUID) -> list[InvestorProfile]:
        result = await self._session.execute(
            select(InvestorProfile)
            .where(
                InvestorProfile.user_id == user_id,
                InvestorProfile.deleted_at.is_not(None),
            )
            .order_by(InvestorProfile.deleted_at.desc())
        )
        return list(result.scalars().all())

    async def get_for_user(self, user_id: uuid.UUID) -> InvestorProfile | None:
        profiles = await self.list_for_user(user_id)
        return profiles[0] if profiles else None

    async def get_owned(
        self, profile_id: uuid.UUID, user_id: uuid.UUID
    ) -> InvestorProfile | None:
        result = await self._session.execute(
            select(InvestorProfile)
            .where(
                InvestorProfile.id == profile_id,
                InvestorProfile.user_id == user_id,
                InvestorProfile.deleted_at.is_(None),
            )
            .options(
                selectinload(InvestorProfile.sector_preferences),
                selectinload(InvestorProfile.stage_preferences),
                selectinload(InvestorProfile.reviews),
            )
        )
        return result.scalar_one_or_none()

    async def restore_owned(
        self, profile_id: uuid.UUID, user_id: uuid.UUID
    ) -> InvestorProfile:
        """Restore an archived profile without erasing its content."""
        from raundo.services.profile_limits import KIND_INVESTOR, ensure_can_create

        profile = await self._session.get(InvestorProfile, profile_id)
        if profile is None or profile.user_id != user_id:
            raise InvestorServiceError("Анкета не найдена.", code="missing")
        if profile.deleted_at is None:
            return profile
        await ensure_can_create(self._session, user_id=user_id, kind=KIND_INVESTOR)
        profile.deleted_at = None
        profile.review_status = REVIEW_DRAFT
        profile.current_revision += 1
        profile.published_snapshot = None
        profile.approved_at = None
        profile.approved_by = None
        await self._session.flush()
        return profile

    async def create_additional_draft(self, user_id: uuid.UUID) -> InvestorProfile:
        """Create an extra investor profile; quota (FREE=1, PRO>1) is enforced."""
        from raundo.services.profile_limits import KIND_INVESTOR, ensure_can_create

        await ensure_can_create(self._session, user_id=user_id, kind=KIND_INVESTOR)
        profile = InvestorProfile(
            id=uuid.uuid4(),
            user_id=user_id,
            geography={},
            seeking=[],
            support_modes=[],
            claims_direct_investment=False,
            open_for_requests=True,
            affiliation_status="none",
            contact_policy="relay",
            review_status=REVIEW_DRAFT,
            current_revision=1,
        )
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def get_or_create_draft(self, user_id: uuid.UUID) -> InvestorProfile:
        existing = await self.get_for_user(user_id)
        if existing is not None:
            return existing
        result = await self._session.execute(
            select(InvestorProfile).where(InvestorProfile.user_id == user_id)
        )
        archived = result.scalar_one_or_none()
        if archived is not None and archived.deleted_at is not None:
            await self._revive_archived_profile(archived)
            loaded = await self.get_for_user(user_id)
            assert loaded is not None
            return loaded
        from raundo.services.profile_limits import KIND_INVESTOR, ensure_can_create

        await ensure_can_create(self._session, user_id=user_id, kind=KIND_INVESTOR)
        profile = InvestorProfile(
            id=uuid.uuid4(),
            user_id=user_id,
            geography={},
            seeking=[],
            support_modes=[],
            claims_direct_investment=False,
            open_for_requests=True,
            affiliation_status="none",
            contact_policy="relay",
            show_contact_after_accept=False,
            check_discuss_individually=False,
            review_status=REVIEW_DRAFT,
            current_revision=1,
        )
        self._session.add(profile)
        await self._session.flush()
        loaded = await self.get_for_user(user_id)
        assert loaded is not None
        return loaded

    async def ensure_editable(self, profile: InvestorProfile) -> None:
        if profile.deleted_at is not None:
            raise InvestorServiceError("Эта анкета удалена.")
        if not can_edit_review_state(profile.review_status):
            raise InvestorServiceError(not_editable_message(profile.review_status))

    async def begin_approved_edit(self, profile: InvestorProfile) -> None:
        await self.ensure_editable(profile)
        await begin_investor_approved_edit(self._session, profile)

    async def prepare_owner_field_write(self, profile: InvestorProfile) -> None:
        await self.ensure_editable(profile)
        if profile.review_status == REVIEW_APPROVED:
            await begin_investor_approved_edit(self._session, profile)

    async def register_owner_change(self, profile: InvestorProfile) -> None:
        await self.ensure_editable(profile)
        await register_investor_owner_change(self._session, profile)

    async def delete_owned(self, profile: InvestorProfile, user_id: uuid.UUID) -> None:
        try:
            await soft_delete_investor(self._session, profile, user_id)
        except ValueError:
            raise InvestorServiceError("Нельзя удалить чужую анкету.") from None

    async def _revive_archived_profile(self, profile: InvestorProfile) -> None:
        from raundo.services.profile_limits import KIND_INVESTOR, ensure_can_create

        await ensure_can_create(self._session, user_id=profile.user_id, kind=KIND_INVESTOR)
        profile.deleted_at = None
        profile.review_status = REVIEW_DRAFT
        profile.published_snapshot = None
        profile.current_revision = 1
        profile.approved_at = None
        profile.approved_by = None
        profile.revoked_at = None
        profile.display_name = None
        profile.investor_type = None
        profile.organization_name = None
        profile.claimed_organization_id = None
        profile.affiliation_status = "none"
        profile.position = None
        profile.strategy_description = None
        profile.support_modes = []
        profile.claims_direct_investment = False
        profile.open_for_requests = True
        profile.min_check_minor = None
        profile.max_check_minor = None
        profile.currency = None
        profile.check_discuss_individually = False
        profile.geography = {}
        profile.public_reference_url = None
        profile.telegram_photo_file_id = None
        profile.telegram_photo_unique_id = None
        profile.contact_policy = "relay"
        profile.contact_username = None
        profile.show_contact_after_accept = False
        profile.seeking = []
        await self._session.execute(
            delete(InvestorSectorPreference).where(
                InvestorSectorPreference.investor_id == profile.id
            )
        )
        await self._session.execute(
            delete(InvestorStagePreference).where(
                InvestorStagePreference.investor_id == profile.id
            )
        )
        await self._session.flush()

    async def set_sectors(self, profile: InvestorProfile, sector_codes: set[str]) -> None:
        await self.prepare_owner_field_write(profile)
        if not sector_codes:
            raise InvestorServiceError("Выберите хотя бы одно направление.")
        sectors = await self._catalog.sectors_by_codes(sector_codes)
        if len(sectors) != len(sector_codes):
            raise InvestorServiceError("Неизвестное направление.")
        await self._session.execute(
            delete(InvestorSectorPreference).where(
                InvestorSectorPreference.investor_id == profile.id
            )
        )
        for sector in sectors:
            self._session.add(
                InvestorSectorPreference(investor_id=profile.id, sector_id=sector.id)
            )
        await self._session.flush()
        await register_investor_owner_change(self._session, profile)

    async def set_stages(self, profile: InvestorProfile, stage_codes: set[str]) -> None:
        await self.prepare_owner_field_write(profile)
        if not stage_codes:
            raise InvestorServiceError("Выберите хотя бы одну стадию.")
        stages = await self._catalog.stages_by_codes(stage_codes)
        if len(stages) != len(stage_codes):
            raise InvestorServiceError("Неизвестная стадия.")
        await self._session.execute(
            delete(InvestorStagePreference).where(
                InvestorStagePreference.investor_id == profile.id
            )
        )
        for stage in stages:
            self._session.add(
                InvestorStagePreference(investor_id=profile.id, stage_id=stage.id)
            )
        await self._session.flush()
        await register_investor_owner_change(self._session, profile)

    async def preference_codes(
        self, profile: InvestorProfile
    ) -> tuple[set[str], set[str]]:
        sector_rows = await self._session.execute(
            select(InvestorSectorPreference.sector_id).where(
                InvestorSectorPreference.investor_id == profile.id
            )
        )
        stage_rows = await self._session.execute(
            select(InvestorStagePreference.stage_id).where(
                InvestorStagePreference.investor_id == profile.id
            )
        )
        sector_ids = set(sector_rows.scalars().all())
        stage_ids = set(stage_rows.scalars().all())
        sectors = await self._catalog.list_sectors()
        stages = await self._catalog.list_stages()
        sector_codes = {s.code for s in sectors if s.id in sector_ids}
        stage_codes = {s.code for s in stages if s.id in stage_ids}
        return sector_codes, stage_codes

    def readiness_errors(
        self, profile: InvestorProfile, sector_codes: set[str], stage_codes: set[str]
    ) -> list[str]:
        errors: list[str] = []
        if not profile.display_name:
            errors.append("нет имени")
        if not profile.investor_type:
            errors.append("нет типа участника")
        if not (profile.support_modes or []):
            errors.append("нет форматов поддержки")
        if profile.investor_type and investor_needs_organization(profile.investor_type):
            if not profile.organization_name:
                errors.append("нужна организация/фонд")
        if not profile.strategy_description:
            errors.append("нет стратегии / описания")
        if not validate_investor_preferences(sector_codes, stage_codes):
            errors.append("нужны направления и стадии")
        if cheque_is_relevant(profile.investor_type, profile.support_modes):
            cheque_err = validate_cheque_bounds(
                min_minor=profile.min_check_minor,
                max_minor=profile.max_check_minor,
                currency=profile.currency,
                discuss=profile.check_discuss_individually,
            )
            if cheque_err:
                errors.append(cheque_err)
        if not profile.geography:
            errors.append("нет географии")
        if not profile.public_reference_url:
            errors.append("нет публичной ссылки")
        if not profile.contact_policy:
            errors.append("не выбран способ связи")
        return errors

    async def set_photo(
        self,
        profile: InvestorProfile,
        *,
        telegram_file_id: str,
        file_unique_id: str,
    ) -> None:
        await self.prepare_owner_field_write(profile)
        if not telegram_file_id or not file_unique_id:
            raise InvestorServiceError("Не удалось сохранить изображение. Отправьте фото ещё раз.")
        profile.telegram_photo_file_id = telegram_file_id
        profile.telegram_photo_unique_id = file_unique_id
        await self._session.flush()
        await register_investor_owner_change(self._session, profile)

    async def clear_photo(self, profile: InvestorProfile) -> None:
        await self.prepare_owner_field_write(profile)
        profile.telegram_photo_file_id = None
        profile.telegram_photo_unique_id = None
        await withdraw_investor_sensitive_public(self._session, profile)
        await register_investor_owner_change(self._session, profile)
        await self._session.flush()

    def sync_mandate_flags(self, profile: InvestorProfile) -> None:
        """Derive self-declared direct-invest claim from support_modes (not verified)."""
        modes = set(profile.support_modes or [])
        profile.claims_direct_investment = bool(modes & DIRECT_INVEST_SUPPORT_MODES)

    async def submit_for_review(self, profile: InvestorProfile, user_id: uuid.UUID) -> InvestorProfile:
        if profile.user_id != user_id:
            raise InvestorServiceError("Нельзя отправить чужую анкету.")
        if profile.deleted_at is not None:
            raise InvestorServiceError("Эта анкета удалена.")
        if not can_submit_review_state(profile.review_status):
            raise InvestorServiceError("Повторная отправка из текущего статуса запрещена.")
        sector_codes, stage_codes = await self.preference_codes(profile)
        errors = self.readiness_errors(profile, sector_codes, stage_codes)
        if errors:
            raise InvestorServiceError("Нельзя отправить: " + ", ".join(errors) + ".")
        if profile.review_status == REVIEW_PENDING_CHANGES:
            profile.current_revision += 1
        profile.review_status = REVIEW_SUBMITTED
        self._session.add(
            InvestorReview(
                id=uuid.uuid4(),
                investor_id=profile.id,
                revision=profile.current_revision,
                state=REVIEW_SUBMITTED,
                created_at=datetime.now(timezone.utc),
            )
        )
        await self._session.flush()
        return profile
