"""Startup draft lifecycle: create, edit, media, submit for review."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from raundo.domain import (
    DESC_MAX,
    MAX_STARTUP_PHOTOS,
    MIN_STARTUP_PHOTOS,
    PITCH_MAX,
    PITCH_MIN,
    REVIEW_APPROVED,
    REVIEW_DRAFT,
    REVIEW_PENDING_CHANGES,
    REVIEW_SUBMITTED,
    can_edit_review_state,
    can_submit_review_state,
    slugify_startup_name,
    validate_startup_photo_count,
)
from raundo.models.startup import Startup, StartupMedia, StartupReview, StartupSector
from raundo.repositories.catalog import CatalogRepository
from raundo.services.premium import PremiumService
from raundo.services.profile_lifecycle import (
    begin_startup_approved_edit,
    register_startup_owner_change,
    soft_delete_startup,
    withdraw_startup_sensitive_public,
)
from raundo.ui_labels import not_editable_message


class StartupServiceError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class StartupService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._catalog = CatalogRepository(session)

    async def get_owned(self, startup_id: uuid.UUID, owner_user_id: uuid.UUID) -> Startup | None:
        result = await self._session.execute(
            select(Startup)
            .where(
                Startup.id == startup_id,
                Startup.owner_user_id == owner_user_id,
                Startup.deleted_at.is_(None),
            )
            .options(
                selectinload(Startup.media),
                selectinload(Startup.sectors),
                selectinload(Startup.reviews),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_owner(self, owner_user_id: uuid.UUID) -> list[Startup]:
        result = await self._session.execute(
            select(Startup)
            .where(Startup.owner_user_id == owner_user_id, Startup.deleted_at.is_(None))
            .options(selectinload(Startup.media))
            .order_by(Startup.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_archived(self, owner_user_id: uuid.UUID) -> list[Startup]:
        result = await self._session.execute(
            select(Startup)
            .where(
                Startup.owner_user_id == owner_user_id,
                Startup.deleted_at.is_not(None),
            )
            .options(selectinload(Startup.media))
            .order_by(Startup.deleted_at.desc())
        )
        return list(result.scalars().all())

    async def restore_owned(
        self, startup_id: uuid.UUID, owner_user_id: uuid.UUID
    ) -> Startup:
        """Restore an archived startup without erasing its content."""
        from raundo.services.profile_limits import KIND_STARTUP, ensure_can_create

        startup = await self._session.get(Startup, startup_id)
        if startup is None or startup.owner_user_id != owner_user_id:
            raise StartupServiceError("Проект не найден.", code="missing")
        if startup.deleted_at is None:
            return startup
        await ensure_can_create(self._session, user_id=owner_user_id, kind=KIND_STARTUP)
        startup.deleted_at = None
        startup.review_state = REVIEW_DRAFT
        startup.current_revision += 1
        startup.published_snapshot = None
        startup.published_at = None
        await self._session.flush()
        return startup

    async def create_draft(self, owner_user_id: uuid.UUID, name: str) -> Startup:
        from raundo.services.profile_limits import KIND_STARTUP, ensure_can_create

        await ensure_can_create(self._session, user_id=owner_user_id, kind=KIND_STARTUP)
        slug = await self._unique_slug(name)
        startup = Startup(
            id=uuid.uuid4(),
            owner_user_id=owner_user_id,
            slug=slug,
            name=name.strip(),
            intents=[],
            audience_codes=[],
            progress_codes=[],
            use_of_funds_codes=[],
            contact_policy="relay",
            show_contact_after_accept=False,
            review_state=REVIEW_DRAFT,
            current_revision=1,
        )
        self._session.add(startup)
        await self._session.flush()
        return await self.get_owned(startup.id, owner_user_id)  # type: ignore[return-value]

    async def _unique_slug(self, name: str) -> str:
        base = slugify_startup_name(name)
        candidate = base
        n = 1
        while True:
            existing = await self._session.execute(select(Startup.id).where(Startup.slug == candidate))
            if existing.scalar_one_or_none() is None:
                return candidate
            n += 1
            candidate = f"{base}-{n}"[:96]

    async def ensure_editable(self, startup: Startup) -> None:
        if startup.deleted_at is not None:
            raise StartupServiceError("Эта анкета удалена.")
        if not can_edit_review_state(startup.review_state):
            raise StartupServiceError(not_editable_message(startup.review_state))

    async def begin_approved_edit(self, startup: Startup) -> None:
        await self.ensure_editable(startup)
        await begin_startup_approved_edit(self._session, startup)

    async def prepare_owner_field_write(self, startup: Startup) -> None:
        """Snapshot approved public view only when a field is actually being written."""
        await self.ensure_editable(startup)
        if startup.review_state == REVIEW_APPROVED:
            await begin_startup_approved_edit(self._session, startup)

    async def register_owner_change(self, startup: Startup) -> None:
        await self.ensure_editable(startup)
        await register_startup_owner_change(self._session, startup)

    async def delete_owned(self, startup: Startup, owner_user_id: uuid.UUID) -> None:
        try:
            await soft_delete_startup(self._session, startup, owner_user_id)
        except ValueError:
            raise StartupServiceError("Нельзя удалить чужой проект.") from None

    async def set_primary_sector(self, startup: Startup, sector_code: str) -> None:
        await self.prepare_owner_field_write(startup)
        sector = await self._catalog.sector_by_code(sector_code)
        if sector is None:
            raise StartupServiceError("Неизвестная категория.")
        startup.primary_sector_id = sector.id
        await self._session.execute(
            delete(StartupSector).where(StartupSector.startup_id == startup.id)
        )
        self._session.add(
            StartupSector(startup_id=startup.id, sector_id=sector.id, is_primary=True)
        )
        await self._session.flush()
        await register_startup_owner_change(self._session, startup)

    async def set_secondary_sectors(self, startup: Startup, sector_codes: set[str]) -> None:
        await self.prepare_owner_field_write(startup)
        if startup.primary_sector_id is None:
            raise StartupServiceError("Сначала выберите основную категорию.")
        sectors = await self._catalog.sectors_by_codes(sector_codes)
        if len(sectors) != len(sector_codes):
            raise StartupServiceError("Одна или несколько категорий неизвестны.")
        primary = startup.primary_sector_id
        await self._session.execute(
            delete(StartupSector).where(
                StartupSector.startup_id == startup.id,
                StartupSector.is_primary.is_(False),
            )
        )
        for sector in sectors:
            if sector.id == primary:
                continue
            self._session.add(
                StartupSector(startup_id=startup.id, sector_id=sector.id, is_primary=False)
            )
        await self._session.flush()
        await register_startup_owner_change(self._session, startup)

    async def set_stage(self, startup: Startup, stage_code: str) -> None:
        await self.prepare_owner_field_write(startup)
        stage = await self._catalog.stage_by_code(stage_code)
        if stage is None:
            raise StartupServiceError("Неизвестная стадия.")
        startup.stage_id = stage.id
        await self._session.flush()
        await register_startup_owner_change(self._session, startup)

    async def add_photo(
        self,
        startup: Startup,
        *,
        telegram_file_id: str,
        file_unique_id: str,
        width: int | None = None,
        height: int | None = None,
        size: int | None = None,
    ) -> StartupMedia:
        """Append one photo. Safe for Telegram albums (concurrent updates)."""
        await self.prepare_owner_field_write(startup)
        # Serialize album frames that may arrive nearly simultaneously.
        await self._session.execute(
            select(Startup.id).where(Startup.id == startup.id).with_for_update()
        )
        result = await self._session.execute(
            select(StartupMedia).where(StartupMedia.startup_id == startup.id)
        )
        media_rows = list(result.scalars().all())
        if any(m.file_unique_id == file_unique_id for m in media_rows):
            raise StartupServiceError("Это изображение уже добавлено.")
        cap = await self._photo_cap_for(startup)
        if len(media_rows) >= cap:
            if cap < MAX_STARTUP_PHOTOS:
                raise StartupServiceError(
                    "Бесплатно в карточке одна фотография. "
                    "Startup PRO открывает до 3: /premium"
                )
            raise StartupServiceError(
                f"Можно загрузить не больше {MAX_STARTUP_PHOTOS} фотографий."
            )
        max_pos = await self._session.scalar(
            select(func.coalesce(func.max(StartupMedia.position), 0)).where(
                StartupMedia.startup_id == startup.id
            )
        )
        media = StartupMedia(
            id=uuid.uuid4(),
            startup_id=startup.id,
            telegram_file_id=telegram_file_id,
            file_unique_id=file_unique_id,
            position=int(max_pos or 0) + 1,
            kind="photo",
            width=width,
            height=height,
            size=size,
            # No content scanner yet — do not claim media was reviewed.
            scan_state="pending",
            visibility="public",
        )
        self._session.add(media)
        await self._session.flush()
        await register_startup_owner_change(self._session, startup)
        return media

    async def remove_photo(self, startup: Startup, file_unique_id: str) -> None:
        await self.prepare_owner_field_write(startup)
        result = await self._session.execute(
            select(StartupMedia).where(
                StartupMedia.startup_id == startup.id,
                StartupMedia.file_unique_id == file_unique_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise StartupServiceError("Фотография не найдена.")
        await self._session.delete(row)
        await withdraw_startup_sensitive_public(self._session, startup)
        await register_startup_owner_change(self._session, startup)
        await self._session.flush()

    async def photo_count(self, startup: Startup) -> int:
        result = await self._session.execute(
            select(StartupMedia.id).where(StartupMedia.startup_id == startup.id)
        )
        return len(result.scalars().all())

    async def _photo_cap_for(self, startup: Startup) -> int:
        """Issue #5: server-side cap. Legacy gallery flag wins; then Startup PRO."""
        from raundo.domain import FREE_PHOTO_CAP, PRO_PHOTO_CAP

        if startup.grandfathered_gallery:
            return PRO_PHOTO_CAP
        if await PremiumService(self._session).has_active_startup_pro(startup.id):
            return PRO_PHOTO_CAP
        return FREE_PHOTO_CAP

    async def public_photo_cap(self, startup: Startup) -> int:
        """Cap for public card display; stored originals are never deleted."""
        return await self._photo_cap_for(startup)

    async def readiness_errors(self, startup: Startup) -> list[str]:
        errors: list[str] = []
        if not startup.name or len(startup.name.strip()) < 2:
            errors.append("нет названия")
        pitch = (startup.one_liner or "").strip()
        if not pitch:
            errors.append("нет короткого питча")
        elif len(pitch) < PITCH_MIN or len(pitch) > PITCH_MAX:
            errors.append(
                f"короткий питч: нужно от {PITCH_MIN} до {PITCH_MAX} символов "
                f"(сейчас {len(pitch)}). Сократите или уточните перед отправкой"
            )
        if not startup.full_description:
            errors.append("нет описания")
        elif len(startup.full_description.strip()) > DESC_MAX:
            errors.append(
                f"описание длиннее {DESC_MAX} символов. Сократите перед отправкой"
            )
        if startup.fundraising_stage == "not_raising" and startup.ask_amount_minor:
            errors.append(
                "указан инвестиционный запрос, но выбрано «не привлекаем раунд». "
                "Уберите сумму или выберите стадию раунда"
            )
        if startup.primary_sector_id is None:
            errors.append("нет основной категории")
        if startup.stage_id is None:
            errors.append("нет стадии")
        has_link = bool(startup.website_url or startup.app_url or startup.demo_url)
        if not has_link and not startup.no_link_reason:
            errors.append("нет сайта/приложения или пояснения")
        count = await self.photo_count(startup)
        if not validate_startup_photo_count(count):
            errors.append(f"нужно от {MIN_STARTUP_PHOTOS} до {MAX_STARTUP_PHOTOS} фото")
        if not startup.intents:
            errors.append("не выбрано, что ищет проект")
        if not (startup.audience_codes or []):
            errors.append("не указана целевая аудитория")
        if not (startup.progress_codes or []):
            errors.append("не указан подтверждённый прогресс")
        if not startup.monetization_status:
            errors.append("не указана монетизация")
        if not startup.fundraising_stage:
            errors.append("не указана стадия привлечения")
        raising = startup.fundraising_stage != "not_raising" or bool(startup.ask_amount_minor)
        if raising and not (startup.use_of_funds_codes or []):
            errors.append("не указано использование средств")
        if not (startup.country or "").strip():
            errors.append("не указана география")
        if not startup.founder_display_name or not startup.founder_role:
            errors.append("нет данных основателя")
        if not startup.contact_policy:
            errors.append("нет политики контакта")
        return errors

    async def submit_for_review(self, startup: Startup, owner_user_id: uuid.UUID) -> Startup:
        if startup.owner_user_id != owner_user_id:
            raise StartupServiceError("Нельзя отправить чужой стартап.")
        if startup.deleted_at is not None:
            raise StartupServiceError("Эта анкета удалена.")
        if not can_submit_review_state(startup.review_state):
            raise StartupServiceError("Повторная отправка из текущего статуса запрещена.")
        errors = await self.readiness_errors(startup)
        if errors:
            raise StartupServiceError("Нельзя отправить: " + ", ".join(errors) + ".")
        if startup.review_state == REVIEW_PENDING_CHANGES:
            startup.current_revision += 1
        startup.review_state = REVIEW_SUBMITTED
        self._session.add(
            StartupReview(
                id=uuid.uuid4(),
                startup_id=startup.id,
                revision=startup.current_revision,
                state=REVIEW_SUBMITTED,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await self._session.flush()
        return startup
