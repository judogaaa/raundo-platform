# RAUNDO — поддержка и модерация

**Канон изменён владельцем (2026-10-27).** Администрирование выполняется в веб-панели RAUNDO Admin, а не в Telegram-чате `/admin`. Пользовательский бот остаётся единственным клиентом для стартапов и инвесторов. См. `docs/ADMIN_WEB.md`.

Команда `/admin` в боте остаётся заглушкой и не является рабочей панелью.

## 1. Главное админ-меню

```text
🛡 RAUNDO · Админка
[📝 Стартапы на проверке] [💼 Инвесторы на проверке]
[🆘 Поддержка] [🚩 Жалобы]
[👥 Пользователи] [🏆 Рейтинги]
[⭐ Подписки/платежи] [📊 Статистика]
[⚙️ Справочники] [📋 Аудит]
```

Все числа реальные либо пустое состояние. Admin Reply Keyboard можно включить только в приватном чате доверенного пользователя, но **нельзя считать UI скрытие защитой**. Every callback checks role and staff capability server-side.

## 2. Проверка стартапов (включая Telegram принадлежность)

- Queue by `submitted/under_review/changes_requested/approved/rejected/suspended`, timestamps and assignment.
- Review full profile, 1–3 photos, title, pitch, sector, stage, site/app, founder team, creator Telegram ID (restricted), submitted website ownership evidence if requested, moderation history.
- `✅ Одобрить и опубликовать` / `✏️ На доработку` / `❌ Отклонить` / `⛔ Скрыть` / `♻️ Восстановить`; negative actions require reason. Show preview before decision and confirm any risky move.
- Approval atomic: record review revision, founder association status, startup published_at, audit, enqueue founder notification; keep safe rollback/review process.
- Never auto-approve based on Telegram nickname, supplied site or paid plan; moderator may ask proof of involvement. Founder can submit revision or appeal. Admin cannot impersonate founder or invent projects in production.

## 3. Проверка инвесторов (отдельная очередь)

- Applicant full form: investor type/company/role/strategy, interests, stages, check range, geography, public sources and private proof if requested. Data only to staff authorized for verification.
- `✅ Подтвердить профиль`, `📎 Запросить подтверждение`, `✏️ Вернуть на доработку`, `❌ Отклонить`, `⛔ Приостановить`; reason + audit + applicant bot message and ability to amend.
- Verified badge means **manual profile review only**, not proof of capital or accredited investor status. Premium cannot skip queue or influence review. Investor verified status revoked if abuse or false claims with due process.
- Only approved investors count as verified investor cohort for leaderboard; they can filter by selected preferences and send labelled verified interest requests.

## 4. Поддержка (в том же боте)

User /support or 🆘 button → category publication/investor verification/contact/bugs/subscription/payments/complaint/other → message and optional attachment → server creates `SUP-000001` (illustrative number) with status/open, sends receipt.

Admin /admin → 🆘 Поддержка → inbox open/in_progress/waiting_user/resolved/closed, conversation and attachments. `💬 Ответить` opens private admin FSM attached to selected ticket, message sent via bot to requester after DB+outbox; `🗒 Заметка` staff-only (never sent). `✅ Решено`, `🔁 Переоткрыть`, category/priority and assignment. /mytickets shows only user's tickets; after user reply, admin notified. Telegram bot blocked → DB keeps message; after unblock user can fetch, no false delivery.

Ticket ≠ interest request ≠ public comment. User support free forever; no 24/7 promise if not staffed.

## 5. Жалобы, юзеры, Premium, аналитика

- Content reports for startup/investor/comment/message, reason, safety triage; restrict/mute/ban with audit and appeal. Reporter private.
- Admin can inspect fraud anomalies, spam floods, flagged offsite links, suspicious investor behavior, rate-limit events. No arbitrary vote injection.
- Admin can view anonymized payments, subscriber statuses, refund queue/support only when payments exist; cannot manufacture successful paid events.
- Dashboard: submissions/published, verified investors, interactions, accepted intros, rank job statuses, tickets backlog, payment receipts/refunds and errors. No fabricated metrics.
- Admin can edit category taxonomy; all modifications versioned to avoid silently changing startup/sector preference semantics.

## 6. RBAC and privacy

`superadmin` (owner), `startup_reviewer`, `investor_reviewer`, `support_agent`, `trust_safety`, `billing_support`; least privilege. Bootstrap superadmin via private numeric Telegram ID, DB mapping, identity validation and audit. No admin in groups/public chats. Sensitive documents encrypted/private, short retention, separate access. User should know what data will be examined before submission.

## 7. Admin notification policy

High priority: new submitted startup, new investor profile, severe report, urgent support, payment dispute. Batch low priority; deduplicate and rate-limit. Moderators can choose queue/digest instead of bot flooding. Staff actions generate user notice (with reason) safely and consistently.

## 8. Tests and done criteria

Both queues independent and fully usable in bot on iPhone and desktop; non-admin cannot open /admin or reuse callback; founder's unapproved project never in public results; fake investor cannot self-verify; ticket replies never leak internal notes; duplicate approve action idempotent; moderator can request extra proof; every review recoverable/audited; revoked investor excluded from next verified leaderboard per policy; blocked bot doesn't delete support history.

## NEW CANON: funding institutions and affiliate review (2026-10-22)

Extend the existing separate "startup" and "investor" queues, **without losing either**, with filtered subqueues by institution type: angel/private, VC/micro-VC, CVC/family office, syndicate/angel network, accelerator/venture studio, public support/development/partner. A staff reviewer must separately approve (a) the organization public profile and stated mandate, (b) Telegram account's affiliation/role to represent it, and (c) claimed individual direct-invest authority when giving investor cohort vote. **A self-claimed job title must not grant fund admin permissions**. One organization may have multiple manually approved reps; unlink on departure. Supports/grants/program profiles get a separate noninvestor badge. Add organization data/source review, independent case history, reason/audit, resubmission/appeal and report/impersonation handling. Admin can search duplicates by site/domain, not auto-merge. Default ID allowlist/role checks still apply to /admin. Full acceptance in `docs/FUNDING_PARTICIPANTS.md`.
