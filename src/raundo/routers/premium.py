"""⭐ Premium: catalog 4×3, subscriptions view, payment-disabled state.

No purchase exists: buttons never grant subscriptions and never imitate
payment success. Test grants are dual-gated (config flag + admin allowlist),
always marked is_test, logged with a reason, and never create fake payments.
All callbacks are untrusted: plans/durations are re-validated server-side and
stale/foreign callbacks are answered, never crash the update path.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardRemove,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.config import Settings, get_settings
from raundo.keyboards import adult_confirm_keyboard
from raundo.models.organization import FundingOrganization
from raundo.models.payments import (
    PROVIDER_STARS,
    PROVIDER_YOOKASSA,
    PaymentOrder,
    PaymentTransaction,
)
from raundo.models.startup import Startup
from raundo.models.user import User
from raundo.premium_catalog import (
    DURATION_CODES,
    DURATION_HOURS,
    DURATION_TITLES,
    PLAN_CODES,
    PLAN_COMMUNITY_PLUS,
    PLAN_INVESTOR_PRO,
    PLAN_STARTUP_PRO,
    PLAN_TITLES,
    is_known_duration,
    is_known_plan,
)
from raundo.premium_texts import (
    PREMIUM_EXPIRY,
    PREMIUM_HOW,
    PREMIUM_INTRO,
    PREMIUM_MINE_EMPTY,
    compare_text,
    plan_features_text,
    plan_overview,
    subscription_line,
)
from raundo.services import payments as payment_service
from raundo.services.age import ADULT_REQUIRED_MESSAGE, is_adult
from raundo.services.consents import ConsentService
from raundo.services.payments import PaymentError
from raundo.services.premium import PremiumService, PremiumServiceError
from raundo.services.startups import StartupService
from raundo.services.yookassa import YooKassaClient, YooKassaError
from raundo.state.consent import ConsentStates

CB_ROOT = "p:root"
CB_COMPARE = "p:plans"
CB_MINE = "p:mine"
CB_HOW = "p:how"
CB_EXPIRY = "p:exp"
CB_DEV = "p:dev"

_CB_PLAN_PREFIX = "p:plan:"
_CB_FEAT_PREFIX = "p:feat:"
_CB_BUY_PREFIX = "p:buy:"
_CB_DEV_GRANT_PREFIX = "p:devg:"
_CB_DEV_GRANT_ST_PREFIX = "p:devg:st:"
_CB_TARGET_ST = "p:ts:"
_CB_TARGET_ORG = "p:to:"
_CB_METHOD = "p:m:"
_CB_STARS_CONFIRM = "p:sg:"
_CB_CHECK = "p:ch:"


class BuyFlow(StatesGroup):
    method = State()

_USER_GRANTABLE_PLANS = (PLAN_COMMUNITY_PLUS, PLAN_INVESTOR_PRO, PLAN_STARTUP_PRO)


def _cb_plan(plan_code: str) -> str:
    return f"{_CB_PLAN_PREFIX}{plan_code}"


def _cb_feat(plan_code: str) -> str:
    return f"{_CB_FEAT_PREFIX}{plan_code}"


def _cb_buy(plan_code: str, duration_code: str) -> str:
    return f"{_CB_BUY_PREFIX}{plan_code}:{duration_code}"


def _cb_dev_grant(plan_code: str, duration_code: str) -> str:
    return f"{_CB_DEV_GRANT_PREFIX}{plan_code}:{duration_code}"


def _back_row(target: str = CB_ROOT, label: str = "⬅️ Назад") -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=label, callback_data=target)]


def _simple_keyboard(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def premium_root_keyboard(*, dev_grants_available: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=PLAN_TITLES[plan], callback_data=_cb_plan(plan))]
        for plan in PLAN_CODES
    ]
    rows.append(
        [
            InlineKeyboardButton(text="📊 Сравнить тарифы", callback_data=CB_COMPARE),
            InlineKeyboardButton(text="📦 Мои подписки", callback_data=CB_MINE),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="ℹ️ Как работает PRO", callback_data=CB_HOW),
            InlineKeyboardButton(text="⏳ Срок действия", callback_data=CB_EXPIRY),
        ]
    )
    if dev_grants_available:
        rows.append([InlineKeyboardButton(text="🧪 Тестовая выдача", callback_data=CB_DEV)])
    return _simple_keyboard(rows)


def plan_keyboard(plan_code: str) -> InlineKeyboardMarkup:
    return _simple_keyboard(
        [
            *[
                [
                    InlineKeyboardButton(
                        text=f"{DURATION_TITLES[d]} ({DURATION_HOURS[d]} ч)",
                        callback_data=_cb_buy(plan_code, d),
                    )
                ]
                for d in DURATION_CODES
            ],
            [InlineKeyboardButton(text="📋 Что входит", callback_data=_cb_feat(plan_code))],
            _back_row(),
        ]
    )


def features_keyboard(plan_code: str) -> InlineKeyboardMarkup:
    return _simple_keyboard(
        [
            [InlineKeyboardButton(text="💳 Сроки и цены", callback_data=_cb_plan(plan_code))],
            _back_row(),
        ]
    )


def buy_keyboard(plan_code: str) -> InlineKeyboardMarkup:
    return _simple_keyboard(
        [
            [InlineKeyboardButton(text="📋 Что входит", callback_data=_cb_feat(plan_code))],
            [InlineKeyboardButton(text="⬅️ К тарифу", callback_data=_cb_plan(plan_code))],
            _back_row(),
        ]
    )


def _dev_grants_available(settings: Settings, telegram_id: int | None) -> bool:
    """Dual gate: explicit config flag AND admin allowlist. Default: off."""
    if not settings.premium_dev_grants_enabled:
        return False
    if telegram_id is None:
        return False
    return settings.is_admin(telegram_id)


def _parse_plan_callback(data: str, prefix: str) -> str | None:
    if not data.startswith(prefix):
        return None
    plan_code = data[len(prefix):]
    return plan_code if is_known_plan(plan_code) else None


def _parse_buy_callback(data: str) -> tuple[str, str] | None:
    if not data.startswith(_CB_BUY_PREFIX):
        return None
    parts = data[len(_CB_BUY_PREFIX):].split(":")
    if len(parts) != 2 or not is_known_plan(parts[0]) or not is_known_duration(parts[1]):
        return None
    return parts[0], parts[1]


async def _edit_or_send(
    callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup
) -> None:
    """Edit in place; fall back to a fresh message when editing is impossible."""
    if callback.message is not None:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
            return
        except Exception:
            pass  # stale/inaccessible message → resend as a new one
        await callback.message.answer(text, reply_markup=keyboard)


# ------------------------------------------------------------- entry points


async def _require_consent(
    message: Message, session: AsyncSession, db_user: User, settings: Settings, state: FSMContext
) -> bool:
    consents = ConsentService(session)
    if await consents.has_required_consents(db_user.id):
        return True
    await state.set_state(ConsentStates.awaiting_acceptance)
    await message.answer("Сначала примите условия. Затем откроется Premium.")
    return False


async def premium_entry(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    settings = get_settings()
    if not await _require_consent(message, session, db_user, settings, state):
        return
    await state.clear()
    await message.answer(PREMIUM_INTRO, reply_markup=ReplyKeyboardRemove())
    await message.answer(
        "Выберите раздел Premium:",
        reply_markup=premium_root_keyboard(
            dev_grants_available=_dev_grants_available(settings, db_user.telegram_id)
        ),
    )


# ------------------------------------------------------------------ callbacks


async def cb_root(callback: CallbackQuery) -> None:
    settings = get_settings()
    await callback.answer()
    telegram_id = callback.from_user.id if callback.from_user else None
    await _edit_or_send(
        callback,
        PREMIUM_INTRO,
        premium_root_keyboard(dev_grants_available=_dev_grants_available(settings, telegram_id)),
    )


async def cb_compare(callback: CallbackQuery) -> None:
    await callback.answer()
    await _edit_or_send(callback, compare_text(), _simple_keyboard([_back_row()]))


async def cb_how(callback: CallbackQuery) -> None:
    await callback.answer()
    await _edit_or_send(callback, PREMIUM_HOW, _simple_keyboard([_back_row()]))


async def cb_expiry(callback: CallbackQuery) -> None:
    await callback.answer()
    await _edit_or_send(callback, PREMIUM_EXPIRY, _simple_keyboard([_back_row()]))


async def cb_plan(callback: CallbackQuery) -> None:
    if callback.data is None:
        await callback.answer("Ссылка устарела.")
        return
    plan_code = _parse_plan_callback(callback.data, _CB_PLAN_PREFIX)
    if plan_code is None:
        await callback.answer("Неизвестный тариф.")
        return
    await callback.answer()
    await _edit_or_send(callback, plan_overview(plan_code), plan_keyboard(plan_code))


async def cb_features(callback: CallbackQuery) -> None:
    if callback.data is None:
        await callback.answer("Ссылка устарела.")
        return
    plan_code = _parse_plan_callback(callback.data, _CB_FEAT_PREFIX)
    if plan_code is None:
        await callback.answer("Неизвестный тариф.")
        return
    await callback.answer()
    await _edit_or_send(callback, plan_features_text(plan_code), features_keyboard(plan_code))


async def _ask_method(
    callback: CallbackQuery, state: FSMContext, plan_code: str, duration_code: str
) -> None:
    settings = get_settings()
    offer = payment_service.rub_offer(plan_code, duration_code)
    if offer is None:
        await callback.answer("Тариф недоступен.", show_alert=True)
        return
    await state.set_state(BuyFlow.method)
    await state.update_data(buy_plan=plan_code, buy_duration=duration_code)
    rows: list[list[InlineKeyboardButton]] = []
    # Digital Premium inside Telegram is sold via Stars only (Telegram rules).
    if payment_service.stars_available(settings):
        rows.append(
            [
                InlineKeyboardButton(
                    text="⭐ Telegram Stars", callback_data=f"{_CB_METHOD}stars"
                )
            ]
        )
    # Card/SBP is a separate provider channel; shown only when it is configured
    # and the bot is not in demo mode.
    if (
        settings.payments_mode != "demo"
        and settings.payments_legal_ready
        and payment_service.provider_available(settings)
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text="💳 Банковская карта / СБП", callback_data=f"{_CB_METHOD}card"
                )
            ]
        )
    rows.append(_back_row(_cb_plan(plan_code), "⬅️ К тарифу"))
    text = (
        f"{offer.title}\n"
        f"{offer.duration_title} · {offer.price_label}\n\n"
        "Выберите способ оплаты."
    )
    if not rows[:-1]:
        text = (
            f"{offer.title}\n"
            f"{offer.duration_title} · {offer.price_label}\n\n"
            "Оплата временно недоступна: платёжный канал ещё не подключён. "
            "Анкету можно отправить бесплатно."
        )
    await _edit_or_send(callback, text, _simple_keyboard(rows))


async def cb_buy(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    """Payment method choice; targets (startup/org) are chosen first."""
    if callback.data is None:
        await callback.answer("Ссылка устарела.")
        return
    if not is_adult(db_user):
        await callback.answer()
        if callback.message is not None:
            await callback.message.answer(
                ADULT_REQUIRED_MESSAGE,
                reply_markup=adult_confirm_keyboard(),
            )
        return
    parsed = _parse_buy_callback(callback.data)
    if parsed is None:
        await callback.answer("Неизвестный тариф или срок.")
        return
    plan_code, duration_code = parsed
    offer = payment_service.rub_offer(plan_code, duration_code)
    if offer is None:
        await callback.answer("Тариф недоступен.", show_alert=True)
        return
    if plan_code == PLAN_STARTUP_PRO:
        startups = list(
            (
                await session.execute(
                    select(Startup)
                    .where(Startup.owner_user_id == db_user.id, Startup.deleted_at.is_(None))
                    .order_by(Startup.created_at)
                )
            ).scalars()
        )
        if not startups:
            await callback.answer(
                "Сначала создайте стартап: /startup.", show_alert=True
            )
            return
        await callback.answer()
        rows = [
            [
                InlineKeyboardButton(
                    text=startup.name[:40],
                    callback_data=f"{_CB_TARGET_ST}{duration_code}:{startup.id}",
                )
            ]
            for startup in startups[:10]
        ]
        rows.append(_back_row(_cb_plan(plan_code), "⬅️ К тарифу"))
        await _edit_or_send(
            callback,
            f"{offer.title} · {offer.duration_title}\n\n"
            "Выберите стартап, к которому привяжется подписка.",
            _simple_keyboard(rows),
        )
        return
    if plan_code == "organization_pro":
        orgs = list(
            (
                await session.execute(
                    select(FundingOrganization)
                    .where(FundingOrganization.created_by_user_id == db_user.id)
                    .order_by(FundingOrganization.created_at)
                )
            ).scalars()
        )
        if not orgs:
            await callback.answer(
                "Сначала создайте организацию в анкете инвестора.", show_alert=True
            )
            return
        await callback.answer()
        rows = [
            [
                InlineKeyboardButton(
                    text=org.name[:40],
                    callback_data=f"{_CB_TARGET_ORG}{duration_code}:{org.id}",
                )
            ]
            for org in orgs[:10]
        ]
        rows.append(_back_row(_cb_plan(plan_code), "⬅️ К тарифу"))
        await _edit_or_send(
            callback,
            f"{offer.title} · {offer.duration_title}\n\n"
            "Выберите организацию, к которой привяжется подписка.",
            _simple_keyboard(rows),
        )
        return
    await callback.answer()
    await _ask_method(callback, state, plan_code, duration_code)


async def cb_target(
    callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    data = callback.data or ""
    prefix = _CB_TARGET_ST if data.startswith(_CB_TARGET_ST) else _CB_TARGET_ORG
    rest = data[len(prefix) :]
    try:
        duration_code, raw_id = rest.split(":", 1)
        target_id = uuid.UUID(raw_id)
    except (ValueError, TypeError):
        await callback.answer("Ссылка устарела.", show_alert=True)
        return
    if duration_code not in DURATION_CODES:
        await callback.answer("Неизвестный срок.", show_alert=True)
        return
    plan_code = PLAN_STARTUP_PRO if prefix == _CB_TARGET_ST else "organization_pro"
    if prefix == _CB_TARGET_ST:
        target = await session.get(Startup, target_id)
        valid = target is not None and target.owner_user_id == db_user.id
    else:
        target = await session.get(FundingOrganization, target_id)
        valid = target is not None and target.created_by_user_id == db_user.id
    if not valid:
        await callback.answer("Объект недоступен.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(
        buy_target_startup=str(target_id) if prefix == _CB_TARGET_ST else None,
        buy_target_org=str(target_id) if prefix == _CB_TARGET_ORG else None,
    )
    await _ask_method(callback, state, plan_code, duration_code)


async def _start_card_payment(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
    plan_code: str,
    duration_code: str,
) -> None:
    settings = get_settings()
    if (
        settings.payments_mode == "demo"
        or not settings.payments_legal_ready
        or not payment_service.provider_available(settings)
    ):
        await callback.answer("Оплата картой пока недоступна.", show_alert=True)
        return
    offer = payment_service.rub_offer(plan_code, duration_code)
    if offer is None:
        await callback.answer("Тариф недоступен.", show_alert=True)
        return
    data = await state.get_data()
    startup_id = data.get("buy_target_startup")
    org_id = data.get("buy_target_org")
    startup_uuid = uuid.UUID(startup_id) if startup_id else None
    org_uuid = uuid.UUID(org_id) if org_id else None
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    order = await session.scalar(
        select(PaymentOrder)
        .where(
            PaymentOrder.user_id == db_user.id,
            PaymentOrder.plan_code == plan_code,
            PaymentOrder.duration_code == duration_code,
            PaymentOrder.payment_provider == PROVIDER_YOOKASSA,
            PaymentOrder.status.in_(
                (payment_service.ORDER_CREATED, payment_service.ORDER_PENDING)
            ),
            PaymentOrder.created_at >= cutoff,
            PaymentOrder.target_startup_id == startup_uuid,
            PaymentOrder.target_organization_id == org_uuid,
        )
        .order_by(PaymentOrder.created_at.desc())
        .limit(1)
    )
    if order is None:
        try:
            order, _token = await payment_service.create_order(
                session,
                user=db_user,
                plan_code=plan_code,
                duration_code=duration_code,
                provider=PROVIDER_YOOKASSA,
                settings=settings,
                startup_id=startup_uuid,
                organization_id=org_uuid,
            )
            await session.commit()
        except payment_service.PaymentError as exc:
            await session.rollback()
            await callback.answer(exc.message, show_alert=True)
            return
    tx = await session.scalar(
        select(PaymentTransaction)
        .where(PaymentTransaction.order_id == order.id)
        .order_by(PaymentTransaction.created_at.desc())
        .limit(1)
    )
    url = (tx.safe_payload or {}).get("confirmation_url") if tx is not None else None
    if not url:
        client = YooKassaClient(
            shop_id=settings.yookassa_shop_id,
            secret_key=settings.yookassa_key(),
            api_url=settings.yookassa_api_url,
        )
        try:
            payment = await client.create_payment(
                amount_minor=order.amount_minor,
                currency=order.currency,
                description=f"RAUNDO {order.order_number}",
                return_url="https://t.me/RaundoAppBot",
                metadata={"order_id": str(order.id), "order_number": order.order_number},
                idempotence_key=f"order:{order.id}",
            )
        except YooKassaError as exc:
            await callback.answer(exc.message, show_alert=True)
            return
        if not payment.confirmation_url:
            await callback.answer("ЮKassa не вернула ссылку на оплату.", show_alert=True)
            return
        url = payment.confirmation_url
        await payment_service.record_pending_transaction(
            session,
            order=order,
            provider=PROVIDER_YOOKASSA,
            provider_payment_id=payment.id,
            amount_minor=order.amount_minor,
            currency=order.currency,
            provider_status=payment.status,
            safe_payload={"confirmation_url": url, "test": payment.test},
        )
        await session.commit()
    await callback.answer()
    await _edit_or_send(
        callback,
        "💳 Банковская карта / СБП\n\n"
        f"{offer.title} · {offer.duration_title}\n"
        f"К оплате: {offer.price_label}\n\n"
        "Откройте страницу ЮKassa и выберите удобный способ: банковская карта, СБП, "
        "СберPay, Т-Банк или ЮMoney. После оплаты вернитесь в бот: Premium активируется "
        "автоматически, либо нажмите «Проверить оплату».",
        _simple_keyboard(
            [
                [InlineKeyboardButton(text="Оплатить картой / СБП", url=url)],
                [
                    InlineKeyboardButton(
                        text="Проверить оплату",
                        callback_data=f"{_CB_CHECK}{order.id}",
                    )
                ],
                _back_row(CB_ROOT, "⬅️ Premium"),
            ]
        ),
    )


async def cb_check_payment(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
) -> None:
    raw = (callback.data or "")[len(_CB_CHECK) :]
    try:
        order_id = uuid.UUID(raw)
    except ValueError:
        await callback.answer("Ссылка устарела.", show_alert=True)
        return
    order = await session.get(PaymentOrder, order_id)
    if order is None or order.user_id != db_user.id:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    if order.status == payment_service.ORDER_PAID:
        await callback.answer("Premium уже активирован.", show_alert=True)
        return
    settings = get_settings()
    tx = await session.scalar(
        select(PaymentTransaction)
        .where(PaymentTransaction.order_id == order.id)
        .order_by(PaymentTransaction.created_at.desc())
        .limit(1)
    )
    if tx is None:
        await callback.answer("Платёж ещё не создан.", show_alert=True)
        return
    client = YooKassaClient(
        shop_id=settings.yookassa_shop_id,
        secret_key=settings.yookassa_key(),
        api_url=settings.yookassa_api_url,
    )
    try:
        payment = await client.get_payment(tx.provider_payment_id)
    except YooKassaError:
        await callback.answer("ЮKassa пока не подтвердила платёж. Попробуйте позже.", show_alert=True)
        return
    if payment.status not in payment_service.PAID_STATUSES or not payment.paid:
        await callback.answer("Платёж ещё не подтверждён.", show_alert=True)
        return
    if str((payment.metadata or {}).get("order_id")) != str(order.id):
        await callback.answer("Платёж не относится к заказу.", show_alert=True)
        return
    try:
        _order, sub, _created = await payment_service.activate_order(
            session,
            order_id=order.id,
            provider=PROVIDER_YOOKASSA,
            provider_payment_id=payment.id,
            charge_id=None,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            provider_status="succeeded",
        )
        await session.commit()
    except payment_service.PaymentError as exc:
        await session.rollback()
        await callback.answer(exc.message, show_alert=True)
        return
    await callback.answer("Оплата подтверждена.")
    if sub is not None:
        await _edit_or_send(
            callback,
            "✅ Premium активирован\n\n"
            f"Тариф: {PLAN_TITLES.get(order.plan_code, order.plan_code)}\n"
            f"Действует до: {sub.expires_at:%d.%m.%Y}.",
            _simple_keyboard([_back_row(CB_ROOT, "⬅️ Premium")]),
        )


async def cb_method(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
    bot: Bot,
) -> None:
    method = (callback.data or "")[len(_CB_METHOD) :]
    data = await state.get_data()
    plan_code = data.get("buy_plan")
    duration_code = data.get("buy_duration")
    if not plan_code or not duration_code:
        await callback.answer("Выберите тариф заново.", show_alert=True)
        return
    if method == "card":
        await _start_card_payment(
            callback, session, db_user, state, str(plan_code), str(duration_code)
        )
        return
    if method != "stars":
        await callback.answer("Неизвестный способ.", show_alert=True)
        return
    settings = get_settings()
    offer = payment_service.rub_offer(str(plan_code), str(duration_code))
    if offer is None:
        await callback.answer("Тариф недоступен.", show_alert=True)
        return
    if not payment_service.stars_available(settings):
        await callback.answer("Оплата Stars пока недоступна.", show_alert=True)
        return
    stars = int(offer.stars_amount or 0)
    await callback.answer()
    await _edit_or_send(
        callback,
        f"⭐ Telegram Stars\n\n"
        f"{offer.title} · {offer.duration_title}\n"
        f"К оплате: {stars} ⭐ (XTR)\n\n"
        "Подтверждая, вы принимаете оферту Premium. Оплата разовая, без автопродления.\n"
        "Возврат рассматривается поддержкой в течение 5 календарных дней.",
        _simple_keyboard(
            [
                [
                    InlineKeyboardButton(
                        text="Подтвердить и оплатить",
                        callback_data=f"{_CB_STARS_CONFIRM}{plan_code}:{duration_code}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📄 Оферта", url=f"{get_settings().checkout_base_url.rstrip('/')}/legal/offer"
                    )
                ],
                _back_row(_cb_plan(str(plan_code)), "⬅️ К тарифу"),
            ]
        ),
    )


async def cb_stars_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
    bot: Bot,
) -> None:
    raw = (callback.data or "")[len(_CB_STARS_CONFIRM) :]
    try:
        plan_code, duration_code = raw.split(":", 1)
    except ValueError:
        await callback.answer("Ссылка устарела.", show_alert=True)
        return
    settings = get_settings()
    if not payment_service.stars_available(settings):
        await callback.answer("Оплата Stars пока недоступна.", show_alert=True)
        return
    offer = payment_service.rub_offer(plan_code, duration_code)
    if offer is None or offer.stars_amount is None:
        await callback.answer("Тариф недоступен.", show_alert=True)
        return
    data = await state.get_data()
    startup_id = data.get("buy_target_startup")
    org_id = data.get("buy_target_org")
    try:
        order, _token = await payment_service.create_order(
            session,
            user=db_user,
            plan_code=plan_code,
            duration_code=duration_code,
            provider=PROVIDER_STARS,
            settings=settings,
            startup_id=uuid.UUID(startup_id) if startup_id else None,
            organization_id=uuid.UUID(org_id) if org_id else None,
        )
        await session.commit()
    except (PaymentError, ValueError) as exc:
        await session.rollback()
        message = exc.message if isinstance(exc, PaymentError) else str(exc)
        await callback.answer(message, show_alert=True)
        return
    await callback.answer()
    await bot.send_invoice(
        chat_id=db_user.telegram_id,
        title=f"{offer.title} · {offer.duration_title}",
        description=", ".join(offer.benefits)[:255],
        payload=str(order.id),
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=offer.title, amount=int(offer.stars_amount or 0))],
    )
    await state.clear()

async def on_pre_checkout(query: PreCheckoutQuery, session: AsyncSession, db_user: User) -> None:
    try:
        order = await session.get(PaymentOrder, uuid.UUID(query.invoice_payload))
    except (ValueError, TypeError):
        order = None
    ok = (
        order is not None
        and order.user_id == db_user.id
        and order.payment_provider == PROVIDER_STARS
        and order.status in {"created", "pending"}
        and query.currency == order.currency
        and query.total_amount == order.amount_minor
    )
    await query.answer(
        ok=ok,
        error_message=None if ok else "Заказ недоступен или сумма изменилась. Откройте /premium.",
    )


async def on_successful_payment(
    message: Message, session: AsyncSession, db_user: User, state: FSMContext
) -> None:
    payment = message.successful_payment
    if payment is None:
        return
    try:
        order_id = uuid.UUID(payment.invoice_payload)
    except (ValueError, TypeError):
        return
    order = await session.get(PaymentOrder, order_id)
    if order is None or order.user_id != db_user.id:
        return
    if payment.currency != "XTR" or payment.total_amount != order.amount_minor:
        return
    charge_id = payment.telegram_payment_charge_id
    try:
        _order, sub, created = await payment_service.activate_order(
            session,
            order_id=order_id,
            provider=PROVIDER_STARS,
            provider_payment_id=charge_id,
            charge_id=charge_id,
            amount_minor=payment.total_amount,
            currency=payment.currency,
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - never claim activation without DB success
        await session.rollback()
        await message.answer(
            "Оплата получена, но активация задерживается. Мы уже разбираемся. "
            "Если подписка не появится, напишите в /support."
        )
        return
    await state.clear()
    if created and sub is not None:
        await message.answer(
            f"✅ Подписка {PLAN_TITLES.get(order.plan_code, 'PRO')} активирована "
            f"до {sub.expires_at:%d.%m.%Y}."
        )
    else:
        await message.answer("Подписка уже активирована по этому платежу.")


async def _show_mine(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    premium = PremiumService(session)
    await premium.expire_overdue()
    subs = await premium.my_subscriptions(db_user.id)
    if not subs:
        await _edit_or_send(callback, PREMIUM_MINE_EMPTY, _simple_keyboard([_back_row()]))
        return
    rows = [
        subscription_line(
            {
                "plan_title": PLAN_TITLES.get(sub.plan_code, sub.plan_code),
                "duration": sub.duration_code,
                "active": sub.status == "active",
                "expires_at": sub.expires_at,
                "price_minor": sub.price_minor_at_purchase,
                "is_test": sub.is_test,
            }
        )
        for sub in subs
    ]
    text = "📦 Мои подписки:\n\n" + "\n".join(rows)
    await _edit_or_send(callback, text, _simple_keyboard([_back_row()]))


async def cb_mine(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    await callback.answer()
    await _show_mine(callback, session, db_user)


# ------------------------------------------------- dev grants (dual-gated)


async def cb_dev(callback: CallbackQuery) -> None:
    settings = get_settings()
    telegram_id = callback.from_user.id if callback.from_user else None
    if not _dev_grants_available(settings, telegram_id):
        await callback.answer("Тестовая выдача отключена.")
        return
    await callback.answer()
    text = (
        "🧪 Тестовая выдача подписки\n\n"
        "• Выдаётся с пометкой «тест», с причиной и вашим Telegram ID.\n"
        "• Не создаёт платежей и не даёт verified/approved статусы.\n"
        "• Organization PRO через интерфейс не выдаётся: привязка к организации "
        "делается сервисным слоем после админ-подтверждения.\n\n"
        "Выберите тариф (срок 30 дней):"
    )
    rows = [
        [
            InlineKeyboardButton(
                text=f"{PLAN_TITLES[p]}: тест",
                callback_data=_cb_dev_grant(p, "30d"),
            )
        ]
        for p in _USER_GRANTABLE_PLANS
    ]
    rows.append(_back_row())
    await _edit_or_send(callback, text, _simple_keyboard(rows))


def _hourly_idem(telegram_id: int, plan_code: str, duration_code: str) -> str:
    """Hourly idempotency bucket: dedupes double-clicks, allows later re-grant."""
    return f"dev:{telegram_id}:{plan_code}:{duration_code}:{int(time.time() // 3600)}"


async def _dev_grant_user_plan(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    plan_code: str,
    duration_code: str,
    telegram_id: int,
) -> None:
    premium = PremiumService(session)
    try:
        sub, created = await premium.grant(
            plan_code=plan_code,
            duration_code=duration_code,
            acting_user_id=db_user.id,
            idempotency_key=_hourly_idem(telegram_id, plan_code, duration_code),
            is_test=True,
            test_reason="dev grant via /premium (admin, dual-gated)",
            granted_by_telegram_id=telegram_id,
        )
    except PremiumServiceError as exc:
        await callback.answer(exc.message, show_alert=True)
        return
    await session.commit()  # persist before any Telegram success UX
    if not created:
        await callback.answer("Подписка уже есть. Повтор не создал новую.")
        return
    await callback.answer(
        f"Тестовая подписка {PLAN_TITLES[plan_code]} выдана "
        f"до {sub.expires_at:%d.%m.%Y %H:%M} UTC.",
        show_alert=True,
    )
    await _show_mine(callback, session, db_user)


async def _dev_grant_startup(
    callback: CallbackQuery, session: AsyncSession, db_user: User, payload: str
) -> None:
    """Startup PRO test grant needs an explicit startup binding."""
    settings = get_settings()
    telegram_id = callback.from_user.id if callback.from_user else None
    if not _dev_grants_available(settings, telegram_id):
        await callback.answer("Тестовая выдача отключена.")
        return
    svc = StartupService(session)
    startups = await svc.list_for_owner(db_user.id)
    if payload.startswith("auto:"):
        duration_code = payload[len("auto:"):]
        if not is_known_duration(duration_code):
            await callback.answer("Неизвестный срок.")
            return
        if not startups:
            await callback.answer("У вас нет стартапов. Сначала создайте анкету.", show_alert=True)
            return
        if len(startups) > 1:
            rows = [
                [
                    InlineKeyboardButton(
                        text=f"{s.name}",
                        callback_data=f"{_CB_DEV_GRANT_ST_PREFIX}{s.id}:{duration_code}",
                    )
                ]
                for s in startups
            ]
            rows.append(_back_row())
            await _edit_or_send(
                callback,
                "Выберите свой стартап для Startup PRO:",
                _simple_keyboard(rows),
            )
            return
        startup_id = startups[0].id
    else:
        raw_id, _, raw_duration = payload.partition(":")
        try:
            startup_id = uuid.UUID(raw_id)
        except ValueError:
            await callback.answer("Некорректный стартап.")
            return
        if not is_known_duration(raw_duration):
            await callback.answer("Неизвестный срок.")
            return
        duration_code = raw_duration
    premium = PremiumService(session)
    try:
        sub, created = await premium.grant(
            plan_code=PLAN_STARTUP_PRO,
            duration_code=duration_code,
            acting_user_id=db_user.id,
            startup_id=startup_id,
            idempotency_key=_hourly_idem(telegram_id, PLAN_STARTUP_PRO, duration_code),
            is_test=True,
            test_reason="dev grant via /premium (admin, dual-gated)",
            granted_by_telegram_id=telegram_id,
        )
    except PremiumServiceError as exc:
        await callback.answer(exc.message, show_alert=True)
        return
    await session.commit()
    if not created:
        await callback.answer("Startup PRO уже активен для этого стартапа.", show_alert=True)
        return
    await callback.answer(
        f"Startup PRO выдан на {DURATION_TITLES[duration_code]} (тест).",
        show_alert=True,
    )


async def cb_dev_grant(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    settings = get_settings()
    telegram_id = callback.from_user.id if callback.from_user else None
    if not _dev_grants_available(settings, telegram_id):
        await callback.answer("Тестовая выдача отключена.")
        return
    if callback.data is None or not callback.data.startswith(_CB_DEV_GRANT_PREFIX):
        await callback.answer("Ссылка устарела.")
        return
    rest = callback.data[len(_CB_DEV_GRANT_PREFIX):]
    if rest.startswith("st:"):
        await _dev_grant_startup(callback, session, db_user, rest[3:])
        return
    parts = rest.split(":")
    if len(parts) != 2 or not is_known_plan(parts[0]) or not is_known_duration(parts[1]):
        await callback.answer("Неизвестные параметры.")
        return
    plan_code, duration_code = parts
    if plan_code == PLAN_STARTUP_PRO:
        await _dev_grant_startup(callback, session, db_user, f"auto:{duration_code}")
        return
    if plan_code not in _USER_GRANTABLE_PLANS:
        await callback.answer("Этот тариф не выдаётся через UI.")
        return
    await _dev_grant_user_plan(callback, session, db_user, plan_code, duration_code, telegram_id)


def create_router() -> Router:
    router = Router(name="premium")
    router.message.register(premium_entry, Command("premium"))
    router.message.register(premium_entry, F.text == "⭐ Premium")
    router.callback_query.register(cb_root, F.data == CB_ROOT)
    router.callback_query.register(cb_compare, F.data == CB_COMPARE)
    router.callback_query.register(cb_how, F.data == CB_HOW)
    router.callback_query.register(cb_expiry, F.data == CB_EXPIRY)
    router.callback_query.register(cb_mine, F.data == CB_MINE)
    router.callback_query.register(cb_dev, F.data == CB_DEV)
    router.callback_query.register(cb_plan, F.data.startswith(_CB_PLAN_PREFIX))
    router.callback_query.register(cb_features, F.data.startswith(_CB_FEAT_PREFIX))
    router.callback_query.register(cb_buy, F.data.startswith(_CB_BUY_PREFIX))
    router.callback_query.register(cb_target, F.data.startswith(_CB_TARGET_ST))
    router.callback_query.register(cb_target, F.data.startswith(_CB_TARGET_ORG))
    router.callback_query.register(cb_method, F.data.startswith(_CB_METHOD))
    router.callback_query.register(cb_stars_confirm, F.data.startswith(_CB_STARS_CONFIRM))
    router.callback_query.register(cb_check_payment, F.data.startswith(_CB_CHECK))
    router.pre_checkout_query.register(on_pre_checkout)
    router.message.register(on_successful_payment, F.successful_payment)
    router.callback_query.register(cb_dev_grant, F.data.startswith(_CB_DEV_GRANT_PREFIX))
    return router
