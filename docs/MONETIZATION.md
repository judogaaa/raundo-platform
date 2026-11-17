# RAUNDO — платные подписки для трёх типов пользователей

## Free core (always)

Free /start, browsing, like/dislike/next, comments, bookmarks baseline, founder startup creation/submission, investor application/manual verification, sector preferences/basic filters, published rankings and basic requests for contact, support and abuse reporting. Not paid: ranking weight, investor "verified", founder approval, initial connection.

## Three optional plans (product hypotheses, not active charges)

| План | Ориентир в RUB за месяц (НЕ цена счёта) | Реальная ценность |
|---|---:|---|
| 👑 **Community PLUS** | 299 ₽ | коллекции избранных, история оценок, персональные уведомления о новых категориях, косметический бейдж без путаницы с verified |
| 🚀 **Startup PRO** | 1 490 ₽ | подробная аналитика реакций/переходов, углублённая история интереса инвесторов (агрегированная и privacy-safe), доп. оформление карточек/материалы в разрешённых лимитах, помощь с питчем (AI позже при корректной обработке данных) |
| 💼 **Investor PRO** | 4 990 ₽ | сохранённые сложные фильтры, более точные уведомления по направлениям, сравнение стартапов в чате, заметки, личная подборка, история воронки интересов |

Цены — предложения для интервью. **Не принимать реальные Stars пока не зафиксированы конкретные XTR цены, сервисные сроки, условия возврата, расходы, налоги и юрисдикция.** В Telegram для цифровых товаров/услуг официально требуется Stars `XTR`. Check current https://core.telegram.org/bots/payments-stars .

## Premium UX

`/premium` / ⭐ Premium → compare Free/PLUS/PRO by role → «Что входит» → «Оформить» (payment invoice ONLY when implemented) → order receipt → subscription status / expiry/cancel / payment support. Telegram invoice from **bot**, no Mini App. A user can have multiple roles; union entitlements and transparent billing for different plans.

## Server ledger

`plans`, `price_versions`, `orders`, `payment_events(provider_payment_charge_id unique)`, `subscriptions`, `entitlements`, `refunds`. Before invoice user agrees to terms; server generates signed order payload. Validate pre_checkout_query within Telegram deadline, amount/currency/plan/version/order, successful_payment confirmed and idempotent, grant entitlements in transaction, send receipt via outbox; refund and cancellation/expiry remove paid features only. Never trust client "paid". Handle Telegram recurring subscriptions only according to actually supported API semantics. `/paysupport`, `/terms`, `/support` required before sales.

## Cannot buy

Verified investor badge; publication approval; additional votes; priority in impartial TOP-10; founder personal contacts; false recommendations; guaranteed introductions/investments; private documents or personal data.

## When shipping

One complete product release targets Premium functionality as part of scope; it should not be enabled for real charges until technically tested and legally ready. Offline mock offers may show «скоро» **only during development** and cannot masquerade as live payment.

## NEW CANON 2026-10-22: premium and institution types

Community PLUS, Startup PRO, Investor PRO remain optional. **Investor PRO is not proof of capital, official fund, affiliation or direct-investment mandate**. Additional organization accounts (VC/CVC/angel syndicate/accelerator/public support) may have professional benefits such as saved filters, program intake and team inbox only if validated and without charging for manual verification, basic requests/intro consent or ranking votes. Shared institutional seat billing needs separate product/privacy/legal signoff, not automatically inferred from an employee subscribing personally. Full participant taxonomy and permission boundaries: `docs/FUNDING_PARTICIPANTS.md`.
