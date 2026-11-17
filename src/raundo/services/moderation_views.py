"""Read models for admin moderation detail screens."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from raundo.domain import (
    STARTUP_MONETIZATION_LABELS,
    format_ask_amount,
    format_cheque_range,
)
from raundo.models.investor import (
    InvestorProfile,
    InvestorReview,
    InvestorSectorPreference,
    InvestorStagePreference,
)
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.models.startup import Startup, StartupMedia, StartupReview
from raundo.models.user import User
from raundo.participants import SUPPORT_MODE_LABELS, participant_type_label
from raundo.repositories.catalog import CatalogRepository
from raundo.services.moderation_decisions import revision_diff
from raundo.ui_labels import affiliation_label, geography_label, review_state_label

_MEMBERSHIP_LABELS = {
    "claimed": "Заявлено, ожидает проверки",
    "under_review": "На проверке",
    "approved": "Подтверждено",
    "revoked": "Отозвано",
}

_ORG_REVIEW_LABELS = {
    "draft": "Черновик",
    "submitted": "На проверке",
    "under_review": "На проверке",
    "approved": "Проверена",
    "changes_requested": "Требуются изменения",
    "rejected": "Отклонена",
}


def _label(mapping: dict[str, str], code: str | None, fallback: str = "Не указано") -> str:
    if not code:
        return fallback
    return mapping.get(code, fallback)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _diff_view(rows: list[tuple[str, str, str]]) -> list[dict]:
    """Human-readable diff rows; empty sides are left for the UI to word."""
    out: list[dict] = []
    for label, before, after in rows:
        if _text(before) == _text(after):
            continue
        out.append({"label": label, "before": _text(before), "after": _text(after)})
    return out


def _startup_fields(startup: Startup, *, stage_label: str | None) -> dict[str, str]:
    return {
        "Название": startup.name or "",
        "Питч": startup.one_liner or "",
        "Описание": startup.full_description or "",
        "География": startup.country or "",
        "Стадия": stage_label or "Не указано",
        "Монетизация": STARTUP_MONETIZATION_LABELS.get(startup.monetization_status or "", "")
        or "Не указано",
        "Запрос": format_ask_amount(startup.ask_amount_minor, startup.ask_currency)
        or "Не указано",
        "Статус анкеты": review_state_label(startup.review_state) or "Не указано",
    }


async def startup_review_detail(session: AsyncSession, review_id: uuid.UUID) -> dict:
    review = await session.get(StartupReview, review_id)
    if review is None:
        raise ValueError("missing")
    result = await session.execute(
        select(Startup)
        .where(Startup.id == review.startup_id)
        .options(selectinload(Startup.reviews), selectinload(Startup.media))
    )
    startup = result.scalar_one_or_none()
    if startup is None:
        raise ValueError("missing")
    owner = await session.get(User, startup.owner_user_id)
    stage_label = None
    if startup.stage_id:
        for stage in await CatalogRepository(session).list_stages():
            if stage.id == startup.stage_id:
                stage_label = stage.label
                break
    current = {
        "name": startup.name,
        "one_liner": startup.one_liner,
        "full_description": startup.full_description,
        "country": startup.country,
        "stage_id": str(startup.stage_id) if startup.stage_id else None,
        "monetization_status": startup.monetization_status,
        "ask_amount_minor": startup.ask_amount_minor,
        "ask_currency": startup.ask_currency,
        "website_url": startup.website_url,
        "app_url": startup.app_url,
        "demo_url": startup.demo_url,
        "founder_display_name": startup.founder_display_name,
        "founder_role": startup.founder_role,
    }
    published = startup.published_snapshot or {}

    def _stage(value: object) -> str:
        if not value:
            return ""
        if str(value) == (str(startup.stage_id) if startup.stage_id else ""):
            return stage_label or ""
        return ""

    def _money(amount: object, currency: object) -> str:
        return format_ask_amount(
            int(amount) if amount is not None else None, str(currency) if currency else None
        ) or ""

    diff_view = _diff_view(
        [
            ("Название", _text(published.get("name")), _text(current["name"])),
            ("Питч", _text(published.get("one_liner")), _text(current["one_liner"])),
            (
                "Описание",
                _text(published.get("full_description")),
                _text(current["full_description"]),
            ),
            ("География", _text(published.get("country")), _text(current["country"])),
            (
                "Стадия",
                _stage(published.get("stage_id")),
                _stage(current["stage_id"]),
            ),
            (
                "Монетизация",
                STARTUP_MONETIZATION_LABELS.get(str(published.get("monetization_status") or ""), ""),
                STARTUP_MONETIZATION_LABELS.get(str(current["monetization_status"] or ""), ""),
            ),
            (
                "Запрос инвестиций",
                _money(published.get("ask_amount_minor"), published.get("ask_currency")),
                _money(current["ask_amount_minor"], current["ask_currency"]),
            ),
            ("Сайт", _text(published.get("website_url")), _text(current["website_url"])),
            ("Приложение", _text(published.get("app_url")), _text(current["app_url"])),
            ("Демо", _text(published.get("demo_url")), _text(current["demo_url"])),
            (
                "Основатель",
                _text(published.get("founder_display_name")),
                _text(current["founder_display_name"]),
            ),
            (
                "Роль основателя",
                _text(published.get("founder_role")),
                _text(current["founder_role"]),
            ),
        ]
    )
    media_rows = await session.execute(
        select(StartupMedia).where(StartupMedia.startup_id == startup.id).order_by(StartupMedia.position)
    )
    media = [
        {
            "position": m.position,
            "kind": m.kind,
            "scan_state": m.scan_state,
            "preview_url": f"/api/media/startups/{startup.id}/photos/{m.position}",
        }
        for m in media_rows.scalars()
    ]
    history = [
        {
            "revision": r.revision,
            "state": r.state,
            "comment": r.comment,
            "submitted_at": r.submitted_at.isoformat(),
        }
        for r in sorted(startup.reviews, key=lambda x: x.submitted_at)
    ]
    return {
        "kind": "startup",
        "startup_id": str(startup.id),
        "review_id": str(review.id),
        "title": startup.name,
        "state": review.state,
        "revision": review.revision,
        "current_revision": startup.current_revision,
        "submitted_at": review.submitted_at.isoformat(),
        "fields": _startup_fields(startup, stage_label=stage_label),
        "has_published": startup.published_snapshot is not None,
        "diff": revision_diff(startup.published_snapshot, current),
        "diff_view": diff_view,
        "media": media,
        "history": history,
        "owner": {
            "username": owner.username if owner else None,
            "name": owner.first_name if owner else None,
            "telegram_id": owner.telegram_id if owner else None,
        },
    }


async def _investor_preference_names(
    session: AsyncSession, profile: InvestorProfile
) -> tuple[list[str], list[str]]:
    catalog = CatalogRepository(session)
    sectors_by_id = {str(s.id): s.name_ru for s in await catalog.list_sectors()}
    stages_by_id = {str(s.id): s.label for s in await catalog.list_stages()}
    sector_rows = await session.execute(
        select(InvestorSectorPreference).where(InvestorSectorPreference.investor_id == profile.id)
    )
    stage_rows = await session.execute(
        select(InvestorStagePreference).where(InvestorStagePreference.investor_id == profile.id)
    )
    sector_names = [
        name
        for row in sector_rows.scalars()
        if (name := sectors_by_id.get(str(row.sector_id)))
    ]
    stage_names = [
        name
        for row in stage_rows.scalars()
        if (name := stages_by_id.get(str(row.stage_id)))
    ]
    return sector_names, stage_names


async def _investor_organization(
    session: AsyncSession, profile: InvestorProfile
) -> dict | None:
    """Organization facts plus the three independent verification states."""
    org = None
    membership = None
    if profile.claimed_organization_id:
        org = await session.get(FundingOrganization, profile.claimed_organization_id)
        if org is not None:
            membership = await session.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == org.id,
                    OrganizationMembership.user_id == profile.user_id,
                )
            )
    if org is None and not profile.organization_name:
        return None
    mandate_verified = bool(
        membership is not None
        and membership.status == "approved"
        and membership.claims_direct_investment_mandate
    )
    return {
        "name": org.name if org else (profile.organization_name or ""),
        "type": participant_type_label(org.participant_type) if org else "",
        "position": profile.position or "",
        "website_url": (org.website_url if org else "") or "",
        "review_status": _label(_ORG_REVIEW_LABELS, org.review_status if org else None),
        "membership_status": _label(
            _MEMBERSHIP_LABELS, membership.status if membership else None
        ),
        "affiliation_status": affiliation_label(profile.affiliation_status) or "",
        "mandate_verified": mandate_verified,
        "mandate_label": "Мандат подтверждён" if mandate_verified else "Полномочия не подтверждены",
    }


async def investor_review_detail(session: AsyncSession, review_id: uuid.UUID) -> dict:
    review = await session.get(InvestorReview, review_id)
    if review is None:
        raise ValueError("missing")
    profile = await session.get(InvestorProfile, review.investor_id)
    if profile is None:
        raise ValueError("missing")
    owner = await session.get(User, profile.user_id)
    history_rows = await session.execute(
        select(InvestorReview)
        .where(InvestorReview.investor_id == profile.id)
        .order_by(InvestorReview.created_at)
    )
    history = [
        {
            "revision": r.revision,
            "state": r.state,
            "comment": r.decision_reason,
            "submitted_at": r.created_at.isoformat(),
        }
        for r in history_rows.scalars()
    ]

    catalog = CatalogRepository(session)
    sector_names_by_id = {str(s.id): s.name_ru for s in await catalog.list_sectors()}
    stage_names_by_id = {str(s.id): s.label for s in await catalog.list_stages()}
    sector_rows = await session.execute(
        select(InvestorSectorPreference).where(InvestorSectorPreference.investor_id == profile.id)
    )
    stage_rows = await session.execute(
        select(InvestorStagePreference).where(InvestorStagePreference.investor_id == profile.id)
    )
    sector_ids = [str(row.sector_id) for row in sector_rows.scalars()]
    stage_ids = [str(row.stage_id) for row in stage_rows.scalars()]
    sector_names = [sector_names_by_id[i] for i in sector_ids if i in sector_names_by_id]
    stage_names = [stage_names_by_id[i] for i in stage_ids if i in stage_names_by_id]

    organization = await _investor_organization(session, profile)
    support_modes = [
        SUPPORT_MODE_LABELS.get(code, "") for code in profile.support_modes or []
    ]
    support_modes = [label for label in support_modes if label]
    cheque = format_cheque_range(
        profile.min_check_minor,
        profile.max_check_minor,
        profile.currency,
        discuss=profile.check_discuss_individually,
    )
    media = []
    if profile.telegram_photo_file_id:
        media.append(
            {
                "position": 1,
                "kind": "photo",
                "scan_state": "ok",
                "preview_url": f"/api/media/investors/{profile.id}/photo",
            }
        )
    org_claim = None
    if organization is not None:
        org_claim = {
            "name": organization["name"],
            "status": organization["membership_status"],
        }

    published = profile.published_snapshot or {}

    def _names(ids: object, mapping: dict[str, str]) -> str:
        if not isinstance(ids, list):
            return ""
        return ", ".join(mapping[str(i)] for i in ids if str(i) in mapping)

    def _cheque_of(values: dict) -> str:
        return format_cheque_range(
            values.get("min_check_minor"),
            values.get("max_check_minor"),
            values.get("currency"),
            discuss=bool(values.get("check_discuss_individually")),
        )

    def _support_of(values: dict) -> str:
        codes = values.get("support_modes") or []
        labels = [SUPPORT_MODE_LABELS.get(str(c), "") for c in codes]
        return ", ".join(label for label in labels if label)

    diff_view = _diff_view(
        [
            ("Имя", _text(published.get("display_name")), _text(profile.display_name)),
            (
                "Тип инвестора",
                participant_type_label(published.get("investor_type") or ""),
                participant_type_label(profile.investor_type or ""),
            ),
            (
                "Организация",
                _text(published.get("organization_name")),
                _text(profile.organization_name),
            ),
            ("Должность", _text(published.get("position")), _text(profile.position)),
            (
                "Стратегия",
                _text(published.get("strategy_description")),
                _text(profile.strategy_description),
            ),
            ("Форматы сотрудничества", _support_of(published), _support_of({"support_modes": profile.support_modes})),
            (
                "Инвестиционный чек",
                _cheque_of(published),
                format_cheque_range(
                    profile.min_check_minor,
                    profile.max_check_minor,
                    profile.currency,
                    discuss=profile.check_discuss_individually,
                ),
            ),
            (
                "География",
                geography_label(published.get("geography")),
                geography_label(profile.geography),
            ),
            (
                "Отрасли",
                _names(published.get("sector_ids"), sector_names_by_id),
                ", ".join(sector_names),
            ),
            (
                "Стадии стартапов",
                _names(published.get("stage_ids"), stage_names_by_id),
                ", ".join(stage_names),
            ),
            (
                "Публичная ссылка",
                _text(published.get("public_reference_url")),
                _text(profile.public_reference_url),
            ),
        ]
    )
    current = dict(published)
    current.update(
        {
            "display_name": profile.display_name,
            "strategy_description": profile.strategy_description,
            "investor_type": profile.investor_type,
            "organization_name": profile.organization_name,
            "position": profile.position,
            "support_modes": list(profile.support_modes or []),
            "min_check_minor": profile.min_check_minor,
            "max_check_minor": profile.max_check_minor,
            "currency": profile.currency,
            "check_discuss_individually": profile.check_discuss_individually,
            "geography": dict(profile.geography or {}),
            "public_reference_url": profile.public_reference_url,
            "sector_ids": sector_ids,
            "stage_ids": stage_ids,
        }
    )
    return {
        "kind": "investor",
        "investor_id": str(profile.id),
        "review_id": str(review.id),
        "title": profile.display_name or "Инвестор",
        "state": review.state,
        "revision": review.revision,
        "current_revision": profile.current_revision,
        "submitted_at": review.created_at.isoformat(),
        "has_published": profile.published_snapshot is not None,
        "fields": {
            "Имя": profile.display_name or "",
            "Тип": participant_type_label(profile.investor_type or ""),
            "Стратегия": profile.strategy_description or "",
            "Чек": cheque or "Не указано",
            "Статус": review_state_label(profile.review_status),
        },
        "profile": {
            "investor_type_label": participant_type_label(profile.investor_type or "")
            or "Не указано",
            "strategy": profile.strategy_description or "",
            "cheque": cheque,
            "sectors": sector_names,
            "stages": stage_names,
            "geography": geography_label(profile.geography),
            "support_modes": support_modes,
            "position": profile.position or "",
            "public_reference_url": profile.public_reference_url or "",
            "open_for_requests": bool(profile.open_for_requests),
        },
        "organization": organization,
        "diff": revision_diff(profile.published_snapshot, current),
        "diff_view": diff_view,
        "media": media,
        "history": history,
        "owner": {
            "username": owner.username if owner else None,
            "name": owner.first_name if owner else None,
            "telegram_id": owner.telegram_id if owner else None,
        },
        "organization_claim": org_claim,
    }
