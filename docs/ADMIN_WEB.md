# RAUNDO Admin

Канон с 2026-10-27: пользовательский интерфейс остаётся в `@RaundoAppBot`. Администрирование выполняется в закрытой веб-панели RAUNDO Admin.

- Один Telegram polling worker.
- Отдельный HTTP API (`src/raundo/admin_api`) не встроен в polling.
- Отдельный delivery worker читает `outbox_events` и вызывает Bot API `sendMessage`, без `getUpdates`.
- Общие модели и `review_guards`.

## Локальный запуск (изолированная БД)

Три базы:

- `raundo` — рабочий бот. Админ API отказывается подключаться к этому имени.
- `raundo_admin_dev` — постоянная локальная панель. Не удаляется `test-all.ps1`.
- `raundo_local_suite` — только регрессионные тесты, может пересоздаваться.

Не подключайте панель к рабочей базе `raundo`.

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://raundo:raundo_local_only@127.0.0.1:55432/raundo_local_suite"
cd D:\Projects\RAUNDO-admin
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts\bootstrap_admin_owner.py owner

E2E Playwright использует отдельную учётную запись `e2e_owner` (`scripts/prep_e2e_admin.py`). Скрипт не меняет пароль человеческого Owner `owner`.
.\.venv\Scripts\python.exe -m uvicorn raundo.admin_api.app:app --host 127.0.0.1 --port 8090
cd admin-web
npm install
npm run dev
```

Панель: `http://127.0.0.1:3000`. API: `http://127.0.0.1:8090`.

Остановка: завершить процессы uvicorn и vite.

## Роли

owner, administrator, moderator, support. Проверки на каждом изменяющем endpoint и через CSRF-заголовок `x-csrf-token`.

Пароли: Argon2id. Сессия: HttpOnly cookie. MFA-поле `totp_secret` есть в схеме; обязательная проверка TOTP для Owner ещё не включена.

Оплата не подключается.
