"""Idempotent local showcase data for RAUNDO (live dev DB only).

Creates clearly marked ДЕМО users, 3 startups, 3 investors, 3 organizations and
local demo images. Uses domain services; never bypasses moderation, quotas or
ownership checks. Demo users have unreachable Telegram ids, so their outbox
notifications are removed after creation (no messages leave the machine).
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import delete, select

from raundo.catalog_seed import ensure_catalog_seeded
from raundo.db.session import get_session_factory
from raundo.models.admin_panel import AdminAccount
from raundo.models.contact import ContactRequest
from raundo.models.feed import StartupReaction
from raundo.models.investor import InvestorProfile
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.models.startup import Startup, StartupMedia, StartupReview
from raundo.models.system import OutboxEvent
from raundo.models.user import User
from raundo.services.accounts import AccountService
from raundo.services.investors import InvestorService
from raundo.services.moderation_decisions import approve_startup
from raundo.services.organizations import OrganizationService
from raundo.services.premium import PremiumService
from raundo.services.startups import StartupService

DEMO_TG = {
    "skillpath": 991_901,
    "flowdesk": 991_902,
    "carepulse": 991_903,
    "angel": 991_911,
    "tech": 991_912,
    "vc": 991_913,
}


async def _purge_demo(session) -> None:
    users = (
        await session.execute(
            select(User.id).where(User.telegram_id.in_(list(DEMO_TG.values())))
        )
    ).scalars().all()
    if not users:
        return
    startup_ids = (
        await session.execute(select(Startup.id).where(Startup.owner_user_id.in_(users)))
    ).scalars().all()
    for sid in startup_ids:
        await session.execute(delete(ContactRequest).where(ContactRequest.startup_id == sid))
        await session.execute(delete(StartupReaction).where(StartupReaction.startup_id == sid))
        await session.execute(delete(StartupMedia).where(StartupMedia.startup_id == sid))
        await session.execute(delete(StartupReview).where(StartupReview.startup_id == sid))
    await session.execute(delete(OutboxEvent).where(OutboxEvent.recipient_user_id.in_(users)))
    await session.execute(delete(Startup).where(Startup.id.in_(startup_ids)))
    investor_ids = (
        await session.execute(
            select(InvestorProfile.id).where(InvestorProfile.user_id.in_(users))
        )
    ).scalars().all()
    await session.execute(delete(InvestorProfile).where(InvestorProfile.id.in_(investor_ids)))
    org_ids = (
        await session.execute(
            select(FundingOrganization.id).where(
                FundingOrganization.created_by_user_id.in_(users)
            )
        )
    ).scalars().all()
    if org_ids:
        await session.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id.in_(org_ids)
            )
        )
        await session.execute(delete(FundingOrganization).where(FundingOrganization.id.in_(org_ids)))
    await session.execute(delete(User).where(User.id.in_(users)))
    await session.flush()


async def _user(session, key: str, first: str, last: str, username: str) -> User:
    return await AccountService(session).bind_telegram_user(
        telegram_id=DEMO_TG[key],
        username=username,
        first_name=first,
        last_name=last,
        language_code="ru",
    )


async def _startup(
    session,
    owner: User,
    *,
    name: str,
    pitch: str,
    description: str,
    sector: str,
    stage: str,
    monetization: str,
    fundraising: str,
    ask_minor: int | None,
    country: str,
    website: str | None,
    photos: list[str],
) -> Startup:
    service = StartupService(session)
    startup = await service.create_draft(owner.id, name)
    startup.one_liner = pitch[:240]
    startup.full_description = description
    await service.set_primary_sector(startup, sector)
    await service.set_stage(startup, stage)
    startup.website_url = website
    startup.no_link_reason = None if website else "Демонстрационный проект без сайта"
    startup.intents = ["investment", "pilot", "partnership"]
    startup.audience_codes = ["b2b_smb", "b2b_enterprise"]
    startup.progress_codes = ["mvp", "pilots"]
    startup.monetization_status = monetization
    startup.monetization_note = "Демонстрационные данные для проверки интерфейса"
    startup.fundraising_stage = fundraising
    startup.ask_amount_minor = ask_minor
    startup.ask_currency = "RUB" if ask_minor else None
    startup.use_of_funds_codes = ["product", "team"] if ask_minor else []
    startup.country = country
    startup.founder_display_name = f"{owner.first_name} {owner.last_name}".strip()
    startup.founder_role = "CEO"
    startup.contact_policy = "relay"
    startup.show_contact_after_accept = False
    if len(photos) > 1:
        await PremiumService(session).grant(
            plan_code="startup_pro",
            duration_code="30d",
            acting_user_id=owner.id,
            startup_id=startup.id,
            is_test=True,
            test_reason="DEMO showcase gallery",
        )
    for file_name in photos:
        await service.add_photo(
            startup,
            telegram_file_id=f"demo:{file_name}",
            file_unique_id=f"demo-{uuid.uuid4().hex[:10]}",
        )
    await session.flush()
    return startup


async def _investor(
    session,
    user: User,
    *,
    name: str,
    investor_type: str,
    strategy: str,
    sectors: set[str],
    stages: set[str],
    cheque: tuple[int, int],
    geography: dict,
    org: FundingOrganization | None = None,
    role: str | None = None,
    photo: str | None = None,
) -> InvestorProfile:
    service = InvestorService(session)
    profile = await service.get_or_create_draft(user.id)
    profile.display_name = name
    profile.investor_type = investor_type
    profile.strategy_description = strategy
    profile.support_modes = ["direct_equity"]
    await service.set_sectors(profile, sectors)
    await service.set_stages(profile, stages)
    profile.min_check_minor, profile.max_check_minor = cheque
    profile.currency = "RUB"
    profile.geography = geography
    profile.public_reference_url = "https://example.com/demo-investor"
    profile.contact_policy = "relay"
    profile.position = role
    if org is not None:
        profile.organization_name = org.name
        profile.claimed_organization_id = org.id
        profile.affiliation_status = "claimed"
        profile.claims_direct_investment = True
    service.sync_mandate_flags(profile)
    if photo:
        await service.set_photo(
            profile,
            telegram_file_id=f"demo:{photo}",
            file_unique_id=f"demo-{uuid.uuid4().hex[:10]}",
        )
    await session.flush()
    return profile


async def _organization(session, user: User, *, name: str, description: str, website: str, kind: str):
    org, membership = await OrganizationService(session).claim_organization(
        user_id=user.id,
        name=name,
        participant_type=kind,
        role="Партнёр",
        claims_direct_investment=True,
    )
    org.description = description
    org.website_url = website
    org.country = "Россия"
    org.review_status = "submitted"
    membership.status = "claimed"
    await session.flush()
    return org


async def main() -> int:
    factory = get_session_factory()
    async with factory() as session:
        await ensure_catalog_seeded(session)
        await _purge_demo(session)

        skill = await _user(session, "skillpath", "ДЕМО", "SkillPath", "demo_skillpath")
        flow = await _user(session, "flowdesk", "ДЕМО", "FlowDesk", "demo_flowdesk")
        care = await _user(session, "carepulse", "ДЕМО", "CarePulse", "demo_carepulse")
        angel_user = await _user(session, "angel", "ДЕМО", "Ангел", "demo_angel")
        tech_user = await _user(session, "tech", "ДЕМО", "Техинвестор", "demo_tech")
        vc_user = await _user(session, "vc", "ДЕМО", "Венчурный", "demo_vc")

        org1 = await _organization(
            session,
            angel_user,
            name="ДЕМО · NorthBridge Ventures",
            description="Демонстрационный фонд ранних стадий: AI, SaaS и EdTech.",
            website="https://example.com/northbridge-demo",
            kind="angel_group",
        )
        org2 = await _organization(
            session,
            tech_user,
            name="ДЕМО · Future Capital Lab",
            description="Демонстрационный фонд B2B, FinTech и инфраструктурного ПО.",
            website="https://example.com/future-capital-demo",
            kind="micro_vc",
        )
        org3 = await _organization(
            session,
            vc_user,
            name="ДЕМО · Vector Growth Fund",
            description="Демонстрационный фонд AI, HealthTech и DeepTech.",
            website="https://example.com/vector-growth-demo",
            kind="vc_fund",
        )

        skill_startup = await _startup(
            session,
            skill,
            name="ДЕМО · SkillPath AI",
            pitch="AI-платформа персонального обучения: программа адаптируется к знаниям и цели.",
            description=(
                "SkillPath AI строит персональные образовательные траектории: короткая "
                "диагностика, план из модулей и практики, пересборка после каждого шага. "
                "Проблема: массовые курсы не учитывают уровень и цель. "
                "Аудитория: специалисты, меняющие профессию, и корпоративные команды. "
                "Модель: подписка и корпоративные лицензии. "
                "Преимущество: прозрачная диагностика и измеримый прогресс. "
                "Команда: продукт, методология и инженерия. Демонстрационная запись."
            ),
            sector="ai_ml",
            stage="mvp",
            monetization="planned",
            fundraising="pre_seed",
            ask_minor=15_000_000,
            country="Россия",
            website="https://example.com/skillpath-demo",
            photos=["skillpath-cover.png", "skillpath-gallery.png"],
        )

        flow_startup = await _startup(
            session,
            flow,
            name="ДЕМО · FlowDesk",
            pitch="Единая платформа задач, клиентов и процессов для небольших компаний.",
            description=(
                "FlowDesk собирает в одном окне задачи, клиентскую базу и операционные "
                "процессы небольшой компании. Вместо разрозненных таблиц и мессенджеров "
                "команда видит единый контекст клиента и статус работ. "
                "Аудитория: сервисные компании и агентства до 50 человек. "
                "Модель: подписка на рабочее место. Демонстрационная запись."
            ),
            sector="saas_b2b",
            stage="launched",
            monetization="active",
            fundraising="seed",
            ask_minor=30_000_000,
            country="Россия",
            website="https://example.com/flowdesk-demo",
            photos=["flowdesk-cover.png", "flowdesk-gallery.png"],
        )

        care_startup = await _startup(
            session,
            care,
            name="ДЕМО · CarePulse",
            pitch="Сервис регулярной голосовой проверки самочувствия и связи с близкими.",
            description=(
                "CarePulse помогает семьям поддерживать ежедневный контакт с пожилыми "
                "родственниками: простые голосовые вопросы, фиксация ответов и уведомление "
                "близких, если человек не выходит на связь. "
                "Важно: демонстрационная концепция, не медицинское изделие и не клиническая "
                "технология. Решение не ставит диагнозы и не заменяет врача. "
                "Аудитория: семьи и социальные сервисы. Модель: семейная подписка."
            ),
            sector="healthtech",
            stage="prototype",
            monetization="not_yet",
            fundraising="not_raising",
            ask_minor=None,
            country="Россия",
            website=None,
            photos=["carepulse-cover.jpg"],
        )

        angel = await _investor(
            session,
            angel_user,
            name="ДЕМО · Частный ангел",
            investor_type="angel",
            strategy=(
                "Инвестирую в основателей на ранних стадиях: AI, SaaS и EdTech. "
                "Важны скорость обучения команды и первые платящие пользователи. "
                "Готов помогать с продуктовой стратегией и первыми продажами."
            ),
            sectors={"ai_ml", "saas_b2b", "edtech"},
            stages={"mvp", "launched"},
            cheque=(1_000_000, 5_000_000),
            geography={"scope": "ru"},
            org=org1,
            role="Партнёр",
            photo="investor-angel.png",
        )
        tech = await _investor(
            session,
            tech_user,
            name="ДЕМО · Технологический инвестор",
            investor_type="micro_vc",
            strategy=(
                "Фокус на B2B, FinTech и инфраструктурном ПО. Смотрю на юнит-экономику, "
                "качество команды и потенциал международного рынка. "
                "Готов участвовать в раундах pre-seed и seed."
            ),
            sectors={"fintech", "saas_b2b", "security"},
            stages={"mvp", "revenue"},
            cheque=(3_000_000, 15_000_000),
            geography={"scope": "world"},
            org=org2,
            role="Инвестиционный менеджер",
            photo="investor-tech.png",
        )
        vc = await _investor(
            session,
            vc_user,
            name="ДЕМО · Венчурный партнёр",
            investor_type="vc_fund",
            strategy=(
                "Инвестирую в AI, HealthTech и DeepTech на стадиях от MVP до Series A. "
                "Представляю демонстрационный фонд; полномочия и мандат не подтверждены "
                "и отображаются в админке как заявленные."
            ),
            sectors={"ai_ml", "healthtech", "industry_robotics"},
            stages={"launched", "revenue", "scaling"},
            cheque=(10_000_000, 50_000_000),
            geography={"scope": "world"},
            org=org3,
            role="Венчурный партнёр",
            photo="investor-vc.png",
        )

        # Startup PRO is granted inside _startup for the 2-photo gallery demo.

        startup_service = StartupService(session)
        await startup_service.submit_for_review(skill_startup, skill.id)
        await startup_service.submit_for_review(flow_startup, flow.id)
        await startup_service.submit_for_review(care_startup, care.id)

        investor_service = InvestorService(session)
        for profile, user in ((angel, angel_user), (tech, tech_user), (vc, vc_user)):
            await investor_service.submit_for_review(profile, user.id)

        admin_actor = await session.scalar(select(AdminAccount).order_by(AdminAccount.created_at))
        if admin_actor is None:
            raise RuntimeError("admin account missing")
        review = await session.scalar(
            select(StartupReview)
            .where(StartupReview.startup_id == skill_startup.id)
            .order_by(StartupReview.submitted_at.desc())
        )
        await approve_startup(session, review_id=review.id, actor_id=admin_actor.id)

        # Demo users are unreachable: remove their notifications so the worker
        # never attempts delivery to non-existent Telegram chats.
        demo_ids = [u.id for u in (skill, flow, care, angel_user, tech_user, vc_user)]
        await session.execute(delete(OutboxEvent).where(OutboxEvent.recipient_user_id.in_(demo_ids)))
        await session.commit()

        print("demo_seeded")
        for startup in (skill_startup, flow_startup, care_startup):
            print(f"startup={startup.name} id={startup.id} state={startup.review_state}")
        for profile in (angel, tech, vc):
            print(f"investor={profile.display_name} id={profile.id} state={profile.review_status}")
        for org in (org1, org2, org3):
            print(f"organization={org.name} id={org.id} state={org.review_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
