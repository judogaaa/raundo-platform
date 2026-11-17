"""Telegram Stars: invoice checks, payment activation, dedupe and refunds."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, PreCheckoutQuery, SuccessfulPayment
from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import raundo.models  # noqa: F401
from raundo.catalog_seed import ensure_catalog_seeded
from raundo.config import Settings
from raundo.db.base import Base
from raundo.models.billing import PremiumSubscription
from raundo.models.payments import PaymentOrder, PaymentTransaction
from raundo.models.user import User
from raundo.routers.premium import on_pre_checkout, on_successful_payment
from raundo.services import payments as pay
from raundo.services.accounts import AccountService
from raundo.services.age import confirm_age
from raundo.services.consents import ConsentService
from raundo.services.stars_refunds import StarsRefundError, refund_stars_order
from tests.fakes import RecordingSession

TG_ID = 777000123
CHARGE_ID = "charge_test_123"


class _State:
    def __init__(self) -> None:
        self.cleared = False

    async def clear(self) -> None:
        self.cleared = True


def _tg_user() -> TgUser:
    return TgUser(id=TG_ID, is_bot=False, first_name="Stars")


def _settings() -> Settings:
    # Stars flags are enabled only inside this isolated test environment.
    return Settings(
        _env_file=None,
        BOT_TOKEN="123456:TEST",
        PAYMENTS_MODE="demo",
        PAYMENTS_STARS_ENABLED="true",
        PAYMENTS_STARS_PRICES_APPROVED="true",
        PAYMENTS_LEGAL_READY="true",
        PAYMENTS_CARD_ENABLED="false",
        ADMIN_TELEGRAM_IDS="999",
    )


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    recording = RecordingSession()
    bot = Bot(token="123456789:AATestTokenForStarsTests_Only", session=recording)
    settings = _settings()
    async with factory() as session:
        await ensure_catalog_seeded(session)
        user = await AccountService(session).bind_telegram_user(
            telegram_id=TG_ID, username="stars", first_name="Stars", last_name=None,
            language_code="ru",
        )
        await ConsentService(session).accept_required(user.id, source="test")
        confirm_age(user, "18_plus")
        await session.commit()
        user_id = user.id
        order, token = await pay.create_order(
            session,
            user=user,
            plan_code="community_plus",
            duration_code="7d",
            provider=pay.PROVIDER_STARS,
            settings=settings,
        )
        await session.commit()
        order_id = order.id
    yield {
        "factory": factory,
        "recording": recording,
        "bot": bot,
        "settings": settings,
        "user_id": user_id,
        "order_id": order_id,
    }
    await bot.session.close()
    await engine.dispose()


def _successful_payment(order: PaymentOrder, charge_id: str = CHARGE_ID) -> SuccessfulPayment:
    return SuccessfulPayment(
        currency="XTR",
        total_amount=order.amount_minor,
        invoice_payload=str(order.id),
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="provider_" + charge_id,
        is_recurring=False,
        is_first_recurring=False,
    )


@pytest.mark.asyncio
async def test_stars_order_is_priced_in_xtr(env) -> None:
    async with env["factory"]() as session:
        order = await session.get(PaymentOrder, env["order_id"])
        assert order is not None
        assert order.payment_provider == pay.PROVIDER_STARS
        assert order.currency == "XTR"
        assert order.amount_minor == 100  # provisional Stars price, not RUB
        assert order.amount_minor != 9900


@pytest.mark.asyncio
async def test_pre_checkout_accepts_matching_order(env) -> None:
    async with env["factory"]() as session:
        user = await session.get(User, env["user_id"])
        order = await session.get(PaymentOrder, env["order_id"])
        query = PreCheckoutQuery(
            id="pcq-1",
            from_user=_tg_user(),
            currency="XTR",
            total_amount=order.amount_minor,
            invoice_payload=str(order.id),
        ).as_(env["bot"])
        await on_pre_checkout(query, session, user)
    answers = [c for c in env["recording"].calls if c.__api_method__ == "answerPreCheckoutQuery"]
    assert answers and answers[0].ok is True


@pytest.mark.asyncio
async def test_pre_checkout_rejects_wrong_amount(env) -> None:
    async with env["factory"]() as session:
        user = await session.get(User, env["user_id"])
        order = await session.get(PaymentOrder, env["order_id"])
        query = PreCheckoutQuery(
            id="pcq-2",
            from_user=_tg_user(),
            currency="XTR",
            total_amount=order.amount_minor + 1,
            invoice_payload=str(order.id),
        ).as_(env["bot"])
        await on_pre_checkout(query, session, user)
    answers = [c for c in env["recording"].calls if c.__api_method__ == "answerPreCheckoutQuery"]
    assert answers and answers[-1].ok is False


@pytest.mark.asyncio
async def test_successful_payment_activates_once_and_stores_charge(env) -> None:
    async with env["factory"]() as session:
        user = await session.get(User, env["user_id"])
        order = await session.get(PaymentOrder, env["order_id"])
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=TG_ID, type="private"),
            from_user=_tg_user(),
            successful_payment=_successful_payment(order),
        ).as_(env["bot"])
        state = _State()
        await on_successful_payment(message, session, user, state)
        await on_successful_payment(message, session, user, state)

        await session.refresh(order)
        subs = (await session.execute(select(PremiumSubscription))).scalars().all()
        tx = (await session.execute(select(PaymentTransaction))).scalars().all()
        assert order.status == pay.ORDER_PAID
        assert len(subs) == 1
        assert len(tx) == 1
        assert tx[0].provider_charge_id == CHARGE_ID


@pytest.mark.asyncio
async def test_refund_uses_refund_star_payment_and_marks_order(env) -> None:
    async with env["factory"]() as session:
        user = await session.get(User, env["user_id"])
        order = await session.get(PaymentOrder, env["order_id"])
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=TG_ID, type="private"),
            from_user=_tg_user(),
            successful_payment=_successful_payment(order),
        ).as_(env["bot"])
        await on_successful_payment(message, session, user, _State())

        await refund_stars_order(session, env["bot"], order, actor_id=999)
        await session.commit()
        await session.refresh(order)
        assert order.status == pay.ORDER_REFUNDED
        refunds = [c for c in env["recording"].calls if c.__api_method__ == "refundStarPayment"]
        assert refunds and refunds[0].telegram_payment_charge_id == CHARGE_ID
        assert refunds[0].user_id == TG_ID

        with pytest.raises(StarsRefundError):
            await refund_stars_order(session, env["bot"], order, actor_id=999)


@pytest.mark.asyncio
async def test_refund_rejects_non_stars_order(env) -> None:
    async with env["factory"]() as session:
        user = await session.get(User, env["user_id"])
        demo_order, _ = await pay.create_order(
            session,
            user=user,
            plan_code="community_plus",
            duration_code="3d",
            provider=pay.PROVIDER_DEMO,
            settings=env["settings"],
        )
        await session.commit()
        with pytest.raises(StarsRefundError) as exc:
            await refund_stars_order(session, env["bot"], demo_order, actor_id=999)
        assert exc.value.code == "not_stars"


async def _activate(session, bot, user, order) -> None:
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=TG_ID, type="private"),
        from_user=_tg_user(),
        successful_payment=_successful_payment(order, charge_id=f"charge_{order.id}"),
    ).as_(bot)
    await on_successful_payment(message, session, user, _State())


@pytest.mark.asyncio
async def test_refund_cancels_new_subscription_and_notifies(env) -> None:
    from raundo.models.system import OutboxEvent

    async with env["factory"]() as session:
        user = await session.get(User, env["user_id"])
        order = await session.get(PaymentOrder, env["order_id"])
        await _activate(session, env["bot"], user, order)
        sub = await session.get(PremiumSubscription, order.premium_subscription_id)
        assert sub is not None and sub.status == "active"

        await refund_stars_order(session, env["bot"], order, actor_id=999)
        await session.commit()
        await session.refresh(sub)
        assert sub.status == "cancelled"
        events = (await session.execute(select(OutboxEvent))).scalars().all()
        assert any(e.kind == "payment.refunded" for e in events)


@pytest.mark.asyncio
async def test_refund_of_extension_restores_previous_expiry(env) -> None:
    async with env["factory"]() as session:
        user = await session.get(User, env["user_id"])
        first = await session.get(PaymentOrder, env["order_id"])
        await _activate(session, env["bot"], user, first)
        sub = await session.get(PremiumSubscription, first.premium_subscription_id)
        expires_after_first = sub.expires_at

        second, _ = await pay.create_order(
            session,
            user=user,
            plan_code="community_plus",
            duration_code="30d",
            provider=pay.PROVIDER_STARS,
            settings=env["settings"],
        )
        await session.commit()
        await _activate(session, env["bot"], user, second)
        await session.refresh(sub)
        await session.refresh(second)
        assert second.premium_subscription_id == sub.id
        assert sub.expires_at > expires_after_first
        assert second.subscription_extended is True
        assert second.subscription_expires_before == expires_after_first

        await refund_stars_order(session, env["bot"], second, actor_id=999)
        await session.commit()
        await session.refresh(sub)
        await session.refresh(second)
        assert sub.expires_at == expires_after_first
        assert sub.status == "active"


@pytest.mark.asyncio
async def test_refund_does_not_touch_another_plan(env) -> None:
    async with env["factory"]() as session:
        user = await session.get(User, env["user_id"])
        community = await session.get(PaymentOrder, env["order_id"])
        await _activate(session, env["bot"], user, community)

        investor, _ = await pay.create_order(
            session,
            user=user,
            plan_code="investor_pro",
            duration_code="7d",
            provider=pay.PROVIDER_STARS,
            settings=env["settings"],
        )
        await session.commit()
        await _activate(session, env["bot"], user, investor)
        investor_sub = await session.get(PremiumSubscription, investor.premium_subscription_id)
        assert investor_sub is not None

        await refund_stars_order(session, env["bot"], community, actor_id=999)
        await session.commit()
        await session.refresh(investor_sub)
        assert investor_sub.status == "active"
        investor_order = await session.get(PaymentOrder, investor.id)
        assert investor_order.status == pay.ORDER_PAID


@pytest.mark.asyncio
async def test_refund_of_first_order_keeps_later_purchase(env) -> None:
    """Refunding the oldest purchase must not delete time paid by a later one."""
    async with env["factory"]() as session:
        user = await session.get(User, env["user_id"])
        first = await session.get(PaymentOrder, env["order_id"])  # community_plus 7d
        await _activate(session, env["bot"], user, first)
        sub = await session.get(PremiumSubscription, first.premium_subscription_id)
        assert sub is not None

        second, _ = await pay.create_order(
            session,
            user=user,
            plan_code="community_plus",
            duration_code="30d",
            provider=pay.PROVIDER_STARS,
            settings=env["settings"],
        )
        await session.commit()
        await _activate(session, env["bot"], user, second)
        await session.refresh(sub)
        expires_after_second = sub.expires_at

        await refund_stars_order(session, env["bot"], first, actor_id=999)
        await session.commit()
        await session.refresh(sub)
        # Only the refunded 7 days disappear; the later 30 days stay paid.
        assert sub.expires_at == expires_after_second - timedelta(hours=168)
        assert sub.status == "active"


@pytest.mark.asyncio
async def test_refund_of_middle_extension_keeps_later_extension(env) -> None:
    """A refund in the middle of a chain removes only its own hours."""
    async with env["factory"]() as session:
        user = await session.get(User, env["user_id"])
        first = await session.get(PaymentOrder, env["order_id"])  # 7d
        await _activate(session, env["bot"], user, first)
        sub = await session.get(PremiumSubscription, first.premium_subscription_id)
        assert sub is not None

        middle, _ = await pay.create_order(
            session,
            user=user,
            plan_code="community_plus",
            duration_code="3d",
            provider=pay.PROVIDER_STARS,
            settings=env["settings"],
        )
        await session.commit()
        await _activate(session, env["bot"], user, middle)

        last, _ = await pay.create_order(
            session,
            user=user,
            plan_code="community_plus",
            duration_code="30d",
            provider=pay.PROVIDER_STARS,
            settings=env["settings"],
        )
        await session.commit()
        await _activate(session, env["bot"], user, last)
        await session.refresh(sub)
        expires_after_last = sub.expires_at

        await refund_stars_order(session, env["bot"], middle, actor_id=999)
        await session.commit()
        await session.refresh(sub)
        assert sub.expires_at == expires_after_last - timedelta(hours=72)
        assert sub.status == "active"

        # The untouched purchases stay paid in the ledger.
        for order_id in (first.id, last.id):
            order = await session.get(PaymentOrder, order_id)
            assert order.status == pay.ORDER_PAID