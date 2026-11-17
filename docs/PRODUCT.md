# RAUNDO — единая спецификация полного Telegram-бота

**Версия документа:** 2026-10-22. **Единый объём релиза:** полный Telegram Bot, без Mini App и без отдельного web интерфейса. Workstreams — порядок разработки, а не урезанные «версии» продукта.

## 1. Видение

RAUNDO — место, где стартапы бесплатно публикуют себя и находят аудиторию и инвесторов, инвесторы видят только выбранные сферы и знакомятся с интересными командами, а обычные люди оценивают идеи и помогают им расти. Результат: **реальное полезное знакомство**, а не лайки ради лайков.

Конкуренция — каталоги стартапов, сообщества, чаты и инвестиционные базы. Не утверждать «аналога нет». Ценностная гипотеза: первые 10–30 секунд в привычном Telegram-чате без внешнего интерфейса.

## 2. Основные пользовательские разделы — ВСЕ обязательны

| Раздел | Базовое поведение | Состояния/дополнения |
|---|---|---|
| 🚀 Оценивать | Одна карточка, лайк/дизлайк/пропуск/сохранение/комментарий/интерес/подробности/сайт | темы, стадии, выбранные фильтры, меньше дублей, пустые состояния |
| 📝 Мой стартап | Полная анкета, 1–3 фото, сайт/приложение, проблематика, отрасль, стадия, потребности | draft/submit/review/published/edit/appeal, аналитика |
| 💼 Инвесторам | Полная анкета инвестора + предпочтения и критерии | review/verified, personalised feed, знакомство |
| 🏆 Топ-10 | Каждые 14 дней выбор сообщества и проверенных инвесторов | архив, период, методика, fairness |
| 👤 Мой профиль | Интересы, моя роль, статусы обеих проверок, мои стартапы, оценки, избранное | настройки приватности и языка |
| ⭐ Premium | Community PLUS / Startup PRO / Investor PRO — необязательные | преимущества, Stars, история платежей, support |
| 🆘 Поддержка | Тикет прямо в боте, переписка и уведомления | категории, статус, файлы, закрытие |
| 🛡 Админка | Доступ только владельцу/авторизованным модераторам через /admin | обе очереди проверок, контент, жалобы, пользователи, подписки, аудит |

## 3. Путь пользователя

Зритель: /start → принять необходимые условия → нажать «🚀 Оценивать» → персональные (если настроены) или общие карточки. Никакой обязательной анкеты инвестора/стартапа для простого просмотра.

Основатель: /startup → полная анкета → фото 1–3 → preview → согласие на публикацию и контактные настройки → админ проверяет аккаунт и проект → published → reactions, заявки, беседы с заинтересованными.

Инвестор: /investor → обязательная полная анкета → отрасли/стадии/география/чек/описание → запрос проверки → админ подтверждает → **по умолчанию только выбранные отрасли** → сохранение/лайк/запрос контакта и общение.

Admin: /admin → очередь стартапов и очередь инвесторов **отдельно**; открыть заявку, проверить доказательства/ссылки → approve / changes requested / reject с причиной; поддержка/жалобы, audit. Ни один подтверждённый статус не приобретается за деньги.

## 4. Верификация: что именно проверяем

Telegram Bot API `from_user.id` показывает, **какой Telegram-аккаунт общается с ботом**, но не доказывает подлинность юридического лица или инвестиционный капитал. RAUNDO проверяет два слоя:
1. Control of Telegram account via user-initiated bot interaction (automatic technical binding).
2. Manual moderator review of claimed startup/company/investor role, evidence and contact authorization; resulting badge **«Анкета проверена RAUNDO»**, tooltip «Проверены сведения, указанные в анкете; не подтверждает финансовую состоятельность и не гарантирует безопасность сделки».

Оба профиля могут быть rejected/changes_requested/suspended; есть исправление и повторная подача. Публиковать непроверенную анкету как «верифицированную» запрещено.

## 5. Голоса и взаимодействие

❤️ like, 👎 dislike, ➡️ skip (нейтральное), ⭐ сохранить, 💬 комментарии, 💼 запрос знакомства, 🌐 сайт. Одна реакция на проект от пользователя, можно изменить; дизлайк — закрытый сигнал, не публичный список плохих проектов. Сам по себе like может вызвать **ненавязчивое агрегированное уведомление**, но не раскрывает контакты. Раскрытие после осознанной взаимной договорённости, предпочтительно 1–2 нажатия.

Контакт: opt-in @username, ссылка для перехода в Telegram с учётом платформенной приватности или безопасный relay от имени бота. Нет обязательного раскрытия номера/email. Контакты стороны управляют и могут отозвать на будущее; уже увиденное нельзя технически «стереть» из памяти получателя, об этом сообщать честно.

## 6. Инвесторская релевантность

Investor sectors multiselect (например AI/ML, SaaS, FinTech, HealthTech, Education, Marketplace, Robotics, Industry, Climate, Consumer), preferred stages, optional check range and location; matching based on canonical tags, not свободном тексте. **Default: показывать только выбранные категории** и подходящие стадии, с clear controls «Все», «Настроить», «Следующий». Слишком строгие фильтры → объяснить, предложить расширить, не показывать неожиданные проекты молча.

Каждый стартап имеет обязательную primary category и optional secondary tags; статусы approved + published. Доступны категории для обычных пользователей, чтобы не смотреть ненужное.

## 7. Рейтинги и подписки

Два независимых рейтинга максимума 10 проектов каждые 14 суток UTC: сообщество и **одобренные инвесторы**. Все голоса настоящие, anti-fraud, версия формулы и snapshot. Premium never affects rankings/verification. Базовые публикации/лайки/комментарии/общение бесплатны. Три добровольные подписки добавляют аналитику, фильтры, коллекции и удобства, а не право голоса и контакта.

## 8. Рост и качество

North Star = `accepted relevant introductions` с разрешённым каналом общения. Важнее количество ответов и возвращаемость инвесторов, чем общий MAU. Сначала реальные стартапы и инвесторы, не fake directory/popularity. Функции полностью входят в релиз; порядок работы задаёт ROADMAP и acceptance gate. Одной верифицированной анкеты нельзя считать «инвестиционной сделкой».

## 9. Чего не делаем

Mini App, WebView, React/frontend app, web admin; приём денег для инвестирования, broker/deal execution, гарантия инвестиций/доходности, платное место в ТОП, продажа данных, сторонние выдуманные инвесторы, спам-рассылки.

## 10. Документация функций

`PROFILES_VERIFICATION`, `BOT_UX`, `DISCOVERY_MATCHING`, `ADMIN_SUPPORT`, `RANKINGS`, `MONETIZATION`, `DATA_MODEL`, `ARCHITECTURE`, `SECURITY_LEGAL`, `ROADMAP`, `STATUS`, `QA`.

## NEW CANON (2026-10-22): ecosystem beyond individual investors

The «💼 Инвесторам» section serves **business angels, private investors, VC and micro/seed funds, corporate venture teams, angel groups/syndicates, family offices, accelerators, venture studios, public development funds and corporate partners**, with **type-specific** registration and independent review. A program/mentor/grant giver is NOT automatically direct investor; only verified direct-investment mandate gets investor leaderboard votes. **Organization profile** is a distinct entity from **human representative**, both reviewed separately; multiple verified representatives per fund; one human vote per startup, no vote multiplier for multiple affiliations. Match founders to fund/angel **and** nonfinancial partnership and grant opportunities by sectors/stages/region/cheque/support mode, explicit intro consent. All this is ONE full Telegram bot. Full canonical contract: [FUNDING_PARTICIPANTS.md](FUNDING_PARTICIPANTS.md).
