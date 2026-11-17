# RAUNDO — единственная техническая архитектура Telegram-бота

**No Mini App.** One Python application with logical modules, optional background worker (same repository), PostgreSQL + Redis.

```text
Telegram Private Chats
       │ slash commands / persistent ReplyKeyboard / inline callbacks/media
       ▼
 Telegram Bot API (HTTPS webhook in prod; single long poller in local)
       ▼
 aiogram 3 routers  ── safe middleware (identity, rate limits, error handling)
       ▼
 Domain services (users, founders, investors, discovery, reactions, intro,
                  rankings, premium, support, admin reviews, security)
       ├──► PostgreSQL (SQLAlchemy2 async + Alembic)
       ├──► Redis (durable FSM, throttling, caching, job locks)
       └──► Outbox + worker (notifications, 14d rank jobs, reminders, Stars sync)
       └──► Telegram restricted media references / protected object storage if needed
```

## Technology direction

Python 3.12+, aiogram3, pydantic-settings, SQLAlchemy2 async/asyncpg, Alembic, Redis, pytest/pytest-asyncio, Ruff; for schedules jobs with persistent DB lock and deterministic time periods. Docker Compose local Postgres/Redis/bot. Managed Postgres and HTTPS webhook on production; no external UI hosting.

## Proposed package layout

```text
src/raundo/
  __init__.py
  main.py                # polling/bootstrap
  config.py              # Pydantic settings and environment validation
  keyboards.py           # persistent and inline keyboard helpers
  routers/
    common.py            # /start, help, settings, profile
    discovery.py         # rate cards / sector filters
    startup.py           # founder wizard, gallery
    investor.py          # investor wizard / preferences
    interaction.py       # likes/dislikes, comments, contact requests
    rankings.py
    premium.py
    support.py
    admin.py             # restricted /admin chat panel
  state/
    startup.py
    investor.py
    comment.py
    support.py
    admin_reply.py
  services/
    accounts.py
    startups.py
    investor_verification.py
    discovery.py
    reactions.py
    intros.py
    rankings.py
    payments.py
    support.py
    moderation.py
    notifications.py
  repositories/
    users.py
    startups.py
    investors.py
    reactions.py
    tickets.py
  models/
    user.py
    investor.py
    startup.py
    interaction.py
    admin.py
    billing.py
  workers/
    tasks.py
    scheduler.py
migrations/
tests/
infra/docker-compose.yml
.github/workflows/ci.yml
pyproject.toml
.env.example
AGENTS.md
docs/
```

Only create executable modules when they work and have tests; do not build inert placeholders and mark all functionality complete.

## Identity and FSM

- User identity from real Telegram Bot API event `from_user.id`, never callback_data or text; usernames mutable. Verify webhook secret/entrypoint and private chats; admin numeric IDs server-only. In long polling only one poller.
- Multi-step founders/investor forms persist draft in PostgreSQL, current FSM state Redis (TTL controlled), allows `Back`, `Cancel`, `Resume`, `Edit`. Telegram messages may be retried/duplicated.
- Callback payload <=64 bytes and includes short action/ref/version, actor checked every time. Answer queries promptly; reconcile stale vs current message.
- Filenames/Telegram file_ids not inherently trusted; validate MIME/size and media URLs, restrict scanning and access. Privacy metadata separated.
- Single authority on policy in services, not handlers or client keyboard; all required verification, approval, consent checks server-side.
- Structured logging without Telegram update raw PII, data source provenance for verification, idempotent event outbox.

## Technical milestones are tasks, NOT product versions

Implement in dependency order: bot foundation → account/profile/durable forms → review queues → discovery/reactions → contacts → rankings → support → Premium/payments → complete integration QA. All sections promised are part of the single full release. No separate "lite" user release. Do not turn on paid billing until verified and legal-ready.

## Operational qualities

Transparent state/error messages, no fake progress. Rate limiting, chat flooding prevention, idempotent DB writes, queue retries 403 blocked / 429 rate limit, health logging/alerts, backup and recovery procedure. Tests with mocked Telegram updates + staging test bot, real iPhone Android desktop smoke. See QA, OPS.
