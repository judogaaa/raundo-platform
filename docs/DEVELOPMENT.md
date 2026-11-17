# Локальная разработка RAUNDO

Следуйте запуску в README. `.env` должен указывать на отдельную БД `raundo_admin_dev`, а `BOT_TOKEN` — на ваш тестовый бот. Не используйте данные существующего сервиса.

## Первый сотрудник

После `alembic upgrade head` запустите `python scripts/bootstrap_admin_owner.py portfolio_owner`. Скрипт создаст владельца и покажет случайный одноразовый пароль; при первом входе его нужно сменить. Пароль не помещайте в Git и не используйте демонстрационные пароли тестов для эксплуатации.

## Проверки PostgreSQL

Создайте отдельную пустую БД `raundo_local_suite`, установите `DATABASE_URL` на неё и выполните `alembic upgrade head`. Затем `RAUNDO_POSTGRES_SUITE_TESTS=1 pytest -q` (в PowerShell переменные задаются через `$env:...`). Тесты изменяют только изолированную тестовую базу. Они намеренно отказываются работать с именем рабочей БД.

## Браузерные тесты

Верните `DATABASE_URL` на отдельную `raundo_admin_dev`, примените миграции и установите Playwright: `cd admin-web`, `npm ci`, `npx playwright install chromium`, `npm run e2e`. API 8091 и интерфейс 3100 запускаются самим Playwright. На Linux путь Python берётся из PATH, при необходимости задайте абсолютный `PYTHON_EXECUTABLE`. На Windows по умолчанию используется `.venv/Scripts/python.exe`.

Сгенерированные `.e2e-credentials.json` и `.e2e-fixtures.json` приватны и игнорируются Git. Первый файл содержит случайные пароли локальных тестовых аккаунтов; не публикуйте его и не используйте в продакшене.
