# RAUNDO — полные анкеты и ручное подтверждение

**Канон:** оба направления проходят ручную проверку админом. Telegram user_id is technical binding only, not passport, ownership or wealth verification. Published verification badge must describe exactly the scope checked.

## 1. Роли и независимые статусы

One Telegram account -> `user.id` internal UUID / telegram_id unique. `viewer` always; `founder` profile and `investor` profile can co-exist. Founder's identity/work affiliation and startup listing can each require review.

Statuses for each submission: `draft`, `submitted`, `under_review`, `changes_requested`, `approved`, `rejected`, `suspended`, `archived`; transitions through DB transaction, admin reason/audit, author notified. Approval applies to particular **revision**; sensitive material changes require re-review, not automatic publish.

## 2. Стартап: обязательная анкета через диалог

1. **Название стартапа** (2–80 chars), уникальный slug/dedupe; team name optional.
2. **Одна строка (pitch)** (20–240 chars): понятная проблема/решение без ложных обещаний.
3. **Описание** (100–500 chars), проблема/клиент и что продукт умеет; в ленте открывается по «Подробнее», не дублируется в компактной карточке.
4. **Категория** primary (из справочника), secondary up to 3, AI/ML — отдельная понятная категория; user может выбрать «Другое» с заявкой нового тега.
5. **Стадия**: Idea/Prototype/MVP/Launched/Revenue/Scaling — единый справочник.
6. **Веб-сайт или приложение**: URL сайта или App Store/Google Play/ссылка на демо (если пока нет, указать «Нет ссылки» и объяснить стадию; не заставлять придумывать). Валидация HTTPS, безопасные domain checks.
7. **Фотографии продукта**: **от 1 до 3 изображений**, пользователь загружает сам в чат. Пояснить какие скриншоты подходят; хранить Telegram file_id плюс метаданные и owner/permissions, проверить тип/лимиты/контент; отдельная icon/logo optional. Не требовать фото основателя.
8. **Что ищет**: инвестиции, пользователей, пилоты, партнёров, команду, обратную связь (multiselect). Investment ask optional: сумма/валюта/срок/цель, без инвестиционных гарантий.
9. **Компания/команда**: имя представителя и его роль, регион/страна, контакты для внутренней проверки и явно отдельное разрешение на раскрытие выбранного Telegram username после подтверждения запроса.
10. **Подтверждение Telegram-аккаунта**: реальный user.id из бота; проверить текущий Telegram @username/ссылку (может отсутствовать); если нет username — предложить relay.
11. **Подтверждение отношения к проекту**: сайт/соцсеть проекта/почта на домене или другой разумный proof **при необходимости модератора**, без избыточных паспортов/справок. Опциональные закрытые доказательства только по запросу, с ограниченным хранением.
12. **Preview всей карточки**, права на фото/документы, правила площадки, submit.

Вся анкета wizard FSM: draft persists DB, **⬅️ Назад / ✏️ Изменить / 💾 Сохранить / ❌ Отмена / ✅ Отправить**, можно вернуться после рестарта. Валидация поля перед следующими шагами. Изображения можно заменить или удалить до submit; должно остаться 1–3.

### Публикация стартапа

Owner submits → `startup_review_case` new revision → admin queue → check media/description/URLs/ownership signal → approve+publish, changes_requested, reject. Founder notified with explicit reason; can resubmit. Verified status refers to «Анкета/проект рассмотрены RAUNDO» with scope, not a government certification. Admin may revoke and audit.

## 3. Инвестор: обязательная анкета, затем проверка

1. **Имя/публичное отображаемое имя**, тип: бизнес-ангел, частный инвестор, представитель фонда, семейного офиса, корпоративный инвестор, акселератор/партнёр.
2. **Компания/фонд/бренд** (для соответствующего типа обязательно), должность и роль.
3. **Кратко о себе/инвестиционной стратегии** (100–1200 chars).
4. **Направления интереса**: обязательный multiselect (1–8) из категорий; по умолчанию лента только этих категорий.
5. **Стадии**: обязательный multiselect idea/prototype/MVP/revenue/scaling (хотя бы одна).
6. **Размер предполагаемого чека**: диапазон min/max и валюта, либо безопасный ответ «Обсуждаю индивидуально»; не считать сумму доказательством ресурсов.
7. **География**: РФ/весь мир/выбранные страны или города; optional details.
8. **Что ищет**: equity deals / пилоты / стратегические партнёрства / менторство / приобретение продукта и другое. В UI не смешивать интерес и обязательство инвестировать.
9. **Публичная ссылка/референс**: личный/фондовый сайт, профессиональный профиль, подтверждаемые кейсы/портфель по выбору; точный набор зависит от типа, false claims flagged.
10. **Контакт Telegram**: определяется из Bot API, username если есть; пользователь выбирает способы связи и согласие на показ только после подтверждённого знакомства (никогда автоматически на ❤️).
11. **Необязательные приватные доказательства** по запросу RAUNDO admin, защищённые ограниченным временем хранения, без публичного доступа.
12. **Preview + Terms/Privacy + submission**. До approval статус `на проверке`.

Верификатор вправе approve/changes_requested/reject/suspend с reason, audit и возможностью апелляции. Badge: «Профиль инвестора проверен RAUNDO» **не означает** аккредитованность/платёжеспособность/добросовестность или гарантию сделки. Verified status ≠ premium subscription.

### До/после проверки

- До: человек имеет обычные права смотреть, оценивать как community и менять анкету; **не выдаёт себя за подтверждённого инвестора** и не влияет на Investor TOP.
- После: получает verified investor label, релевантную подборку и право учитываться в рейтинге Investor TOP; может отправлять `💼 Инвестор заинтересован` как verified investor (запросы необязаны быть платными).
- Основателю показывать тип инвестора/интересы/проверенный статус; можно отказаться или пожаловаться.

## 4. Мой профиль: обязательно

User display name, telegram handle optional, language, roles; status review for founder/startup/investor; «Мои проекты», «Мои реакции», «Избранное», «Заявки на знакомство», «Настройки направлений», «Кому доступен контакт», «Premium», «Поддержка», «Удалить/экспортировать данные», «История сообщений поддержки». Edit fields triggers review only when relevant.

## 5. Автоматические уведомления

`startup.submitted/changes_requested/approved/rejected`, `investor.submitted/changes_requested/approved/rejected`, interest request/accepted/declined, reply to comment, ticket answer, rank published, subscription activity. Обязательно dedupe and opt-out for nontransactional messages.

## 6. Acceptance

Founder submits valid **1–3 photos** and complete fields, admin approves, profile appears. Investor submits sectors/stages/check/role, admin approves, filtered feed shows matching only; modifying sector changes selection; rejected investor not verified. No public phone/email from either application; no ability to bypass review via callback or fake Telegram username. Clear resubmission path and revocation.

## NEW CANON: full type-specific financier/partner profiles (2026-10-22)

The investor form above is a common base, not one uniform form. Add type-aware branches for: **angel/private**, **VC/micro VC**, **CVC**, **angel network/syndicate lead**, **family office**, **accelerator/incubator**, **venture studio**, **corporate strategic partner**, **public grant/development fund**. Distinguish legal/operational participant type and `support_modes` (direct investment, co-investment, grants, pilots, mentoring, programme). Investment cheque required only when claiming direct-invest mandate; grants/programmes use different terms and opening/eligibility fields. Sector/stage selections still mandatory and default feed strict. For any organization profile, independently verify organization and that Telegram user **may represent it**; never accept a typed organization name alone. Badges match review scope, no guarantee of wealth/commitment. Preserve old persisted investor_type values via explicit backwards compatible migration; see **[FUNDING_PARTICIPANTS.md](FUNDING_PARTICIPANTS.md)**. This must be addressed in Issue #2/#8 before final gate.
