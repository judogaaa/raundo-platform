"""Idempotent E2E fixtures on the configured dev DB (not production raundo)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update

from raundo.catalog_seed import ensure_catalog_seeded
from raundo.db.session import get_session_factory
from raundo.domain import REVIEW_SUBMITTED
from raundo.models.admin_panel import (
    AdminAccount,
    AdminInvite,
    Complaint,
    SupportMessage,
    SupportTicket,
    UserRestriction,
)
from raundo.models.investor import InvestorProfile
from raundo.models.organization import FundingOrganization, OrganizationMembership
from raundo.models.startup import Startup
from raundo.services.accounts import AccountService
from raundo.services.consents import CONSENT_PROFILE_PUBLICATION, REQUIRED_KINDS, ConsentService
from raundo.services.investors import InvestorService
from raundo.services.startups import StartupService
from raundo.testing.e2e_guard import assert_e2e_db_url

TICKET_NUMBER = "SUP-E2E-001"
TICKET_CLARIFY_NUMBER = "SUP-E2E-002"
TICKET_CONFIRMED_NUMBER = "SUP-E2E-003"
COMPLAINT_NUMBER = "CMP-E2E-001"
FIXTURES_PATH = __import__("pathlib").Path(__file__).resolve().parents[1] / "admin-web" / ".e2e-fixtures.json"



async def _bind_demo_user(session, **values):
    """Explicit consent for fictional E2E actors; never used for real users."""
    user = await AccountService(session).bind_telegram_user(**values)
    for kind in (*REQUIRED_KINDS, CONSENT_PROFILE_PUBLICATION):
        await ConsentService(session).grant(user.id, kind, source="e2e_fixture")
    return user


async def _fill_minimal_startup(startup, svc: StartupService) -> None:
    startup.one_liner = "x" * 50
    startup.full_description = "y" * 100
    await svc.set_primary_sector(startup, "ai_ml")
    await svc.set_stage(startup, "mvp")
    startup.website_url = "https://example.com"
    startup.intents = ["feedback"]
    startup.audience_codes = ["b2c"]
    startup.progress_codes = ["mvp"]
    startup.monetization_status = "not_yet"
    startup.fundraising_stage = "not_raising"
    startup.country = "Россия"
    startup.founder_display_name = "E2E Founder"
    startup.founder_role = "CEO"
    await svc.add_photo(
        startup,
        telegram_file_id="e2e-file-id",
        file_unique_id=f"e2e-uniq-{uuid.uuid4().hex[:8]}",
    )


async def _ensure_pending_startup(
    session,
    owner_id,
    name_prefix: str,
) -> str:
    pending = await session.scalar(
        select(Startup).where(
            Startup.name.like(f"{name_prefix}%"),
            Startup.review_state == REVIEW_SUBMITTED,
        )
    )
    if pending is not None:
        return pending.name
    svc = StartupService(session)
    title = f"{name_prefix}{uuid.uuid4().hex[:6]}"
    startup = await svc.create_draft(owner_id, title)
    await _fill_minimal_startup(startup, svc)
    await svc.submit_for_review(startup, owner_id)
    print(f"e2e_startup_seeded title={title}")
    return title


async def _ensure_screenshot_startup(session, owner_id) -> str:
    """Dedicated pending startup for moderation-card screenshots, never approved by other tests."""
    from raundo.models.startup import StartupReview

    stale = await session.scalars(
        select(Startup).where(Startup.name.like("E2E Card %"))
    )
    for s in stale:
        await session.execute(delete(StartupReview).where(StartupReview.startup_id == s.id))
        await session.delete(s)
    svc = StartupService(session)
    title = f"E2E Card {uuid.uuid4().hex[:6]}"
    startup = await svc.create_draft(owner_id, title)
    await _fill_minimal_startup(startup, svc)
    await svc.submit_for_review(startup, owner_id)
    print(f"e2e_moderation_card_seeded title={title}")
    return title


LONG_STRATEGY = (
    "Инвестируем в основателей, которые решают измеримую проблему на большом рынке. "
    "Фокус — B2B SaaS, финтех-инфраструктура и прикладной ИИ на ранних стадиях.\n\n"
    "Что для нас важно: скорость обучения команды, юнит-экономика на пилотах и готовность "
    "к быстрому масштабированию после подтверждения спроса. Мы не инвестируем в проекты без "
    "работающего прототипа и без первых платящих клиентов.\n\n"
    "После сделки помогаем с наймом CTO, выходом на корпоративных заказчиков и подготовкой "
    "следующего раунда. Готовы участвовать в совете директоров и ежеквартально пересматривать "
    "план по метрикам."
)


async def _fill_card_investor(profile, svc: InvestorService, *, strategy: str, cheque: tuple[int, int]) -> None:
    profile.display_name = "E2E Card Investor"
    profile.investor_type = "vc_fund"
    profile.support_modes = ["direct_equity", "board_seat"]
    profile.strategy_description = strategy
    await svc.set_sectors(profile, {"ai_ml", "fintech", "saas_b2b"})
    await svc.set_stages(profile, {"mvp", "revenue"})
    profile.min_check_minor, profile.max_check_minor = cheque
    profile.currency = "RUB"
    profile.check_discuss_individually = False
    profile.geography = {"scope": "ru"}
    profile.public_reference_url = "https://example.com/e2e-capital"
    profile.contact_policy = "relay"
    profile.position = "Партнёр"
    svc.sync_mandate_flags(profile)
    await svc.set_photo(
        profile,
        telegram_file_id="e2e-card-inv-file",
        file_unique_id=f"e2e-card-inv-uniq-{uuid.uuid4().hex[:8]}",
    )


async def _ensure_investor_cards(session, admin_actor_id) -> tuple[str, str]:
    """Dedicated investor scenarios: fresh application with org claim, and revision 2."""
    from raundo.models.admin_panel import AdminAuditLog
    from raundo.models.investor import InvestorReview
    from raundo.models.system import OutboxEvent
    from raundo.services.moderation_decisions import approve_investor
    from raundo.services.organizations import OrganizationService
    from raundo.services.profile_lifecycle import begin_investor_approved_edit

    # Clean previous runs (fixtures only, dev DB).
    old_profiles = await session.scalars(
        select(InvestorProfile).where(
            InvestorProfile.display_name.in_(["E2E Card Investor", "E2E Revision Investor"])
        )
    )
    old_profiles = list(old_profiles)
    for p in old_profiles:
        await session.execute(delete(InvestorReview).where(InvestorReview.investor_id == p.id))
        await session.execute(
            delete(AdminAuditLog).where(
                AdminAuditLog.resource_type == "investor",
                AdminAuditLog.resource_id == str(p.id),
            )
        )
        await session.execute(
            delete(OutboxEvent).where(
                OutboxEvent.resource_type == "investor",
                OutboxEvent.resource_id == str(p.id),
            )
        )
        await session.delete(p)
    old_orgs = await session.scalars(
        select(FundingOrganization).where(FundingOrganization.normalized_name == "e2e capital")
    )
    for o in list(old_orgs):
        await session.execute(
            delete(OrganizationMembership).where(OrganizationMembership.organization_id == o.id)
        )
        await session.delete(o)
    await session.flush()

    card_user = await _bind_demo_user(session,
        telegram_id=991_011,
        username="e2e_card_investor",
        first_name="Карточка",
        last_name="Инвестор",
        language_code="ru",
    )
    rev_user = await _bind_demo_user(session,
        telegram_id=991_012,
        username="e2e_revision_investor",
        first_name="Повторная",
        last_name="Редакция",
        language_code="ru",
    )
    svc = InvestorService(session)

    # 1. Fresh application: long strategy, big cheque, organization with unconfirmed mandate.
    card = await svc.get_or_create_draft(card_user.id)
    await _fill_card_investor(card, svc, strategy=LONG_STRATEGY, cheque=(100_000_000, 1_000_000_000))
    org, membership = await OrganizationService(session).claim_organization(
        user_id=card_user.id,
        name="E2E Capital",
        participant_type="vc_fund",
        role="Партнёр",
        claims_direct_investment=True,
    )
    membership.status = "claimed"
    org.review_status = "submitted"
    card.claimed_organization_id = org.id
    card.organization_name = org.name
    card.affiliation_status = "claimed"
    card.position = "Партнёр"
    card.review_status = "draft"
    await svc.submit_for_review(card, card_user.id)
    print(f"e2e_investor_card_seeded name={card.display_name}")

    # 2. Second revision: approved, then edited and resubmitted → human diff.
    rev = await svc.get_or_create_draft(rev_user.id)
    await _fill_card_investor(rev, svc, strategy="Инвестируем в финтех на стадии MVP.", cheque=(50_000_000, 200_000_000))
    rev.display_name = "E2E Revision Investor"
    rev.organization_name = "E2E Revision Capital"
    rev.affiliation_status = "none"
    rev.review_status = "draft"
    await svc.submit_for_review(rev, rev_user.id)
    review = await session.scalar(
        select(InvestorReview)
        .where(InvestorReview.investor_id == rev.id)
        .order_by(InvestorReview.created_at.desc())
    )
    await approve_investor(session, review_id=review.id, actor_id=admin_actor_id)
    await begin_investor_approved_edit(session, rev)
    rev.strategy_description = (
        "Инвестируем в финтех и инфраструктуру платежей на стадии MVP и revenue.\n\n"
        "Новая редакция: добавили направления B2B SaaS и прикладной ИИ, подняли верхнюю границу чека."
    )
    rev.min_check_minor, rev.max_check_minor = (100_000_000, 500_000_000)
    await svc.set_sectors(rev, {"ai_ml", "fintech", "saas_b2b"})
    await svc.submit_for_review(rev, rev_user.id)
    print(f"e2e_investor_revision_seeded name={rev.display_name} revision={rev.current_revision}")
    return card.display_name, rev.display_name


async def _complete_investor(profile, svc: InvestorService) -> None:
    profile.display_name = "E2E Investor"
    profile.investor_type = "angel"
    profile.support_modes = ["direct_equity"]
    profile.strategy_description = "s" * 100
    await svc.set_sectors(profile, {"ai_ml", "fintech"})
    await svc.set_stages(profile, {"mvp", "revenue"})
    profile.check_discuss_individually = True
    profile.geography = {"scope": "ru"}
    profile.public_reference_url = "https://example.com/me"
    profile.contact_policy = "relay"
    svc.sync_mandate_flags(profile)
    await svc.set_photo(
        profile,
        telegram_file_id="e2e-inv-file",
        file_unique_id=f"e2e-inv-uniq-{uuid.uuid4().hex[:8]}",
    )


async def _ensure_pending_investor(session, owner_id) -> str:
    pending = await session.scalar(
        select(InvestorProfile).where(
            InvestorProfile.display_name.like("E2E Investor%"),
            InvestorProfile.review_status == REVIEW_SUBMITTED,
            InvestorProfile.deleted_at.is_(None),
        )
    )
    if pending is not None:
        return pending.display_name
    svc = InvestorService(session)
    profile = await svc.get_or_create_draft(owner_id)
    profile.display_name = f"E2E Investor {uuid.uuid4().hex[:6]}"
    await _complete_investor(profile, svc)
    await svc.submit_for_review(profile, owner_id)
    print(f"e2e_investor_seeded name={profile.display_name}")
    return profile.display_name


async def _ensure_pending_organization(session, claimant_user_id) -> str:
    from raundo.services.organizations import OrganizationService

    # Remove leftovers from previous runs so the queue stays clean or repeatable.
    old = await session.scalars(
        select(FundingOrganization).where(
            FundingOrganization.normalized_name.like("e2e fund %"),
        )
    )
    for stale in old:
        await session.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id == stale.id,
            )
        )
        await session.delete(stale)

    svc = OrganizationService(session)
    name = f"E2E Fund {uuid.uuid4().hex[:6]}"
    org, membership = await svc.claim_organization(
        user_id=claimant_user_id,
        name=name,
        participant_type="venture_fund",
        role="Managing Partner",
        claims_direct_investment=True,
    )
    org.review_status = REVIEW_SUBMITTED
    print(f"e2e_organization_seeded name={name} membership={membership.status}")
    return name


async def _purge_resolved_e2e_startups(session) -> None:
    """Reset seeded moderation fixtures so quota and titles stay stable per run."""
    from raundo.models.contact import ContactRequest
    from raundo.models.feed import StartupReaction
    from raundo.models.startup import StartupMedia, StartupReview

    rows = (
        await session.execute(
            select(Startup).where(
                Startup.name.like("E2E Approve %")
                | Startup.name.like("E2E Changes %")
                | Startup.name.like("E2E Card %")
                | Startup.name.like("E2E Startup %")
            )
        )
    ).scalars().all()
    for startup in rows:
        await session.execute(
            delete(ContactRequest).where(ContactRequest.startup_id == startup.id)
        )
        await session.execute(
            delete(StartupReaction).where(StartupReaction.startup_id == startup.id)
        )
        await session.execute(delete(StartupMedia).where(StartupMedia.startup_id == startup.id))
        await session.execute(delete(StartupReview).where(StartupReview.startup_id == startup.id))
        await session.delete(startup)
    await session.flush()


async def _ensure_e2e_plan(session, user_id, plan_code: str) -> None:
    """E2E fixture accounts are Premium so the seeder can create several profiles."""
    from datetime import timedelta

    from raundo.models.billing import PremiumSubscription

    now = datetime.now(timezone.utc)
    existing = await session.scalar(
        select(PremiumSubscription).where(
            PremiumSubscription.user_id == user_id,
            PremiumSubscription.plan_code == plan_code,
            PremiumSubscription.status == "active",
            PremiumSubscription.expires_at > now,
        )
    )
    if existing is not None:
        return
    session.add(
        PremiumSubscription(
            id=uuid.uuid4(),
            owner_type="user",
            user_id=user_id,
            plan_code=plan_code,
            duration_code="30d",
            price_minor_at_purchase=0,
            currency_at_purchase="RUB",
            status="active",
            source="test_grant",
            is_test=True,
            test_reason="E2E fixtures need multiple profiles",
            idempotency_key=f"e2e-plan:{user_id}:{plan_code}",
            starts_at=now,
            expires_at=now + timedelta(days=30),
        )
    )
    await session.flush()


async def main() -> int:
    import os

    # Fixture accounts are Premium; the limit is raised only inside this seeder
    # process so several test profiles can exist without touching prod rules.
    os.environ.setdefault("RAUNDO_PROFILE_LIMIT_PRO", "3")
    assert_e2e_db_url(os.environ.get("DATABASE_URL", ""))
    factory = get_session_factory()
    async with factory() as session:
        await ensure_catalog_seeded(session)
        # Accounts created by the invite E2E spec are disposable fixtures.
        for stale_account in list(
            (await session.execute(select(AdminAccount).where(AdminAccount.login.like("e2e_inv_%")))).scalars()
        ):
            await session.delete(stale_account)
        await session.execute(delete(AdminInvite).where(AdminInvite.login.like("e2e_inv_%")))
        await session.flush()
        founder = await _bind_demo_user(session,
            telegram_id=991_002,
            username="e2e_founder",
            first_name="E2E",
            last_name="Founder",
            language_code="ru",
        )
        investor_user = await _bind_demo_user(session,
            telegram_id=991_004,
            username="e2e_investor_user",
            first_name="E2E",
            last_name="Investor",
            language_code="ru",
        )
        target_user = await _bind_demo_user(session,
            telegram_id=991_007,
            username="e2e_complaint_target",
            first_name="Нарушитель",
            last_name="E2E",
            language_code="ru",
        )
        action_user = await _bind_demo_user(session,
            telegram_id=991_008,
            username="e2e_action_user",
            first_name="Действие",
            last_name="E2E",
            language_code="ru",
        )
        restricted_user = await _bind_demo_user(session,
            telegram_id=991_009,
            username="e2e_restricted_user",
            first_name="Ограниченный",
            last_name="E2E",
            language_code="ru",
        )
        if restricted_user.status != "restricted":
            restricted_user.status = "restricted"
        restriction = await session.scalar(
            select(UserRestriction).where(
                UserRestriction.user_id == restricted_user.id,
                UserRestriction.active.is_(True),
            )
        )
        if restriction is None:
            session.add(
                UserRestriction(
                    id=uuid.uuid4(),
                    user_id=restricted_user.id,
                    kind="blocked",
                    reason="E2E: демонстрация активного ограничения доступа",
                    active=True,
                )
            )
        await _purge_resolved_e2e_startups(session)
        await _ensure_e2e_plan(session, founder.id, "startup_pro")
        await _ensure_e2e_plan(session, investor_user.id, "investor_pro")
        await _ensure_e2e_plan(session, action_user.id, "organization_pro")
        approve_title = await _ensure_pending_startup(session, founder.id, "E2E Approve ")
        changes_title = await _ensure_pending_startup(session, founder.id, "E2E Changes ")
        card_title = await _ensure_screenshot_startup(session, founder.id)
        investor_name = await _ensure_pending_investor(session, investor_user.id)
        organization_name = await _ensure_pending_organization(session, action_user.id)
        admin_actor = await session.scalar(select(AdminAccount).order_by(AdminAccount.created_at).limit(1))
        if admin_actor is None:
            raise RuntimeError("no admin account for fixture decisions; run prep_e2e_admin.py first")
        investor_card_name, investor_revision_name = await _ensure_investor_cards(
            session, admin_actor.id
        )

        complaint = await session.scalar(
            select(Complaint).where(Complaint.public_number == COMPLAINT_NUMBER)
        )
        if complaint is None:
            complaint = Complaint(
                id=uuid.uuid4(),
                public_number=COMPLAINT_NUMBER,
                reporter_user_id=founder.id,
                target_type="user",
                target_id=str(target_user.id),
                reason=(
                    "E2E жалоба: оскорбления и угрозы в личных сообщениях. "
                    "Прошу проверить переписку и принять меры."
                ),
                priority="high",
                attachment_kind="photo",
                attachment_size=4096,
                status="open",
            )
            session.add(complaint)
            await session.flush()
            print("e2e_complaint_seeded")
        else:
            # Every run starts from a clean, repeatable state.
            complaint.status = "open"
            complaint.resolution_comment = None
            complaint.resolved_by = None
            complaint.resolved_at = None
            complaint.reporter_user_id = founder.id
            complaint.target_id = str(target_user.id)
            from raundo.models.admin_panel import AdminAuditLog

            await session.execute(
                delete(AdminAuditLog).where(
                    AdminAuditLog.resource_type == "complaint",
                    AdminAuditLog.resource_id == str(complaint.id),
                )
            )
        if action_user.status == "restricted":
            await session.execute(
                update(UserRestriction)
                .where(UserRestriction.user_id == action_user.id)
                .values(active=False)
            )
            action_user.status = "active"

        ticket = await session.scalar(select(SupportTicket).where(SupportTicket.public_number == TICKET_NUMBER))
        if ticket is None:
            owner = await _bind_demo_user(session,
                telegram_id=991_003,
                username="e2e_support_user",
                first_name="Support",
                last_name="User",
                language_code="ru",
            )
            ticket = SupportTicket(
                id=uuid.uuid4(),
                public_number=TICKET_NUMBER,
                user_id=owner.id,
                subject="E2E обращение",
                status="new",
                priority="normal",
                updated_at=datetime.now(timezone.utc),
            )
            session.add(ticket)
            await session.flush()
            session.add(
                SupportMessage(
                    id=uuid.uuid4(),
                    ticket_id=ticket.id,
                    author_kind="user",
                    body="Тестовое сообщение E2E",
                )
            )
            print("e2e_ticket_seeded")
        else:
            from raundo.models.admin_panel import AdminAuditLog

            await session.execute(
                delete(SupportMessage).where(SupportMessage.ticket_id == ticket.id)
            )
            await session.execute(
                delete(AdminAuditLog).where(
                    AdminAuditLog.resource_type == "ticket",
                    AdminAuditLog.resource_id == str(ticket.id),
                )
            )
            ticket.status = "new"
            ticket.resolved_at = None
            ticket.resolved_by_id = None
            ticket.category = "account"
            ticket.updated_at = datetime.now(timezone.utc)
            session.add(
                SupportMessage(
                    id=uuid.uuid4(),
                    ticket_id=ticket.id,
                    author_kind="user",
                    body="Тестовое сообщение E2E",
                )
            )

        clarify = await session.scalar(
            select(SupportTicket).where(SupportTicket.public_number == TICKET_CLARIFY_NUMBER)
        )
        if clarify is None:
            clarify = SupportTicket(
                id=uuid.uuid4(),
                public_number=TICKET_CLARIFY_NUMBER,
                user_id=ticket.user_id,
                subject="E2E уточнение",
                status="new",
                priority="normal",
                category="startup",
                updated_at=datetime.now(timezone.utc),
            )
            session.add(clarify)
            await session.flush()
        else:
            await session.execute(
                delete(SupportMessage).where(SupportMessage.ticket_id == clarify.id)
            )
            clarify.status = "new"
            clarify.resolved_at = None
            clarify.resolved_by_id = None
            clarify.updated_at = datetime.now(timezone.utc)
        session.add(
            SupportMessage(
                id=uuid.uuid4(),
                ticket_id=clarify.id,
                author_kind="user",
                body="Не хватает данных: какая стадия проекта?",
            )
        )

        confirmed = await session.scalar(
            select(SupportTicket).where(SupportTicket.public_number == TICKET_CONFIRMED_NUMBER)
        )
        if confirmed is None:
            confirmed = SupportTicket(
                id=uuid.uuid4(),
                public_number=TICKET_CONFIRMED_NUMBER,
                user_id=ticket.user_id,
                subject="E2E решённое обращение",
                status="resolved",
                priority="normal",
                category="other",
                updated_at=datetime.now(timezone.utc),
            )
            session.add(confirmed)
            await session.flush()
        else:
            await session.execute(
                delete(SupportMessage).where(SupportMessage.ticket_id == confirmed.id)
            )
        now = datetime.now(timezone.utc)
        confirmed.status = "resolved"
        confirmed.confirmed_at = now
        confirmed.confirmed_by_user_id = confirmed.user_id
        confirmed.resolved_at = now
        confirmed.resolved_by_id = None
        confirmed.disputed_at = None
        confirmed.confirmation_round = 1
        confirmed.updated_at = now
        session.add_all(
            [
                SupportMessage(
                    id=uuid.uuid4(),
                    ticket_id=confirmed.id,
                    author_kind="user",
                    body="Как поменять тариф?",
                ),
                SupportMessage(
                    id=uuid.uuid4(),
                    ticket_id=confirmed.id,
                    author_kind="staff",
                    body="Ответ поддержки E2E: тариф меняется в разделе Premium.",
                ),
                SupportMessage(
                    id=uuid.uuid4(),
                    ticket_id=confirmed.id,
                    author_kind="system",
                    body="Пользователь подтвердил, что вопрос решён.",
                ),
            ]
        )
        await session.commit()
    import json

    FIXTURES_PATH.write_text(
        json.dumps(
            {
                "startupTitle": approve_title,
                "startupChangesTitle": changes_title,
                "investorDisplayName": investor_name,
                "ticketNumber": TICKET_NUMBER,
                "ticketClarifyNumber": TICKET_CLARIFY_NUMBER,
                "ticketConfirmedNumber": TICKET_CONFIRMED_NUMBER,
                "complaintNumber": COMPLAINT_NUMBER,
                "complaintTargetTelegramId": 991_007,
                "actionUserTelegramId": 991_008,
                "restrictedUserTelegramId": 991_009,
                "organizationName": organization_name,
                "moderationCardTitle": card_title,
                "investorCardName": investor_card_name,
                "investorRevisionName": investor_revision_name,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
