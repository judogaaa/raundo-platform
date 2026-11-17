# RAUNDO — типы инвесторов, фондов и организаций поддержки стартапов

**Принято как расширение единого полноценного Telegram-бота: 2026-10-22.** Это функциональное требование полного релиза, не Mini App и не «отдельная версия». Подробности текущей реализации в открытом PR #11 могут отставать от данного дизайна; модели/миграции меняет Cursor в своей feature-ветке. **Документ о требованиях, не реализованный код.**

## 1. Главный принцип: не всё, что помогает стартапу, является инвестором

RAUNDO каталогизирует два независимых признака:
- `participant_type`: *кто это юридически/организационно* — индивидуальный ангел, фонд, синдикат, организация и т. п.
- `support_modes`: *что конкретно предлагает* — прямое equity/convertible/SAFE (если применимо по юрисдикции), займ, grant/subsidy, ускорительная программа, пилот, корпорация как клиент, менторство, co-investment, syndicate lead, strategic partnership, venture building, nonfinancial support.

**Не присваивать «инвестор» только потому, что лицо заполнило анкету акселератора/инкубатора.** Для допуска в рейтинг «Выбор инвесторов» нужны подтверждённые полномочия на прямую инвестиционную оценку/мандат, а не платный тариф или красивое название организации. Профиль банка, грантового оператора или клуба без собственного капитала может быть проверен как **«Партнёр RAUNDO / организация проверена»**, но это не то же самое, что проверенный инвестор.

## 2. Типы профилей и ключевое отличие

| Тип `code` | Отображение в Telegram | Механика и поля, дополнительные к общим |
|---|---|---|
| `angel` | 👤 Бизнес-ангел | Вкладывает собственные средства; опыт, портфель/референсы по желанию, диапазон личного чека, инвестирует сам/с другими, lead/follow |
| `private` | 💼 Частный инвестор | Собственный капитал, стратегия, чек, направления, информация для добровольной проверки |
| `angel_group` | 🤝 Клуб бизнес-ангелов | Участники, официальный организатор, контакты, как рассматривают заявки, капитал участников vs самого клуба |
| `syndicate` | 🤝 Синдикат / syndicate lead | ФИО/команда lead, заявка от участника, резервирование капитала ещё не обещание финансирования, нужен ли lead/follow |
| `vc_fund` | 🏦 Венчурный фонд (VC) | Название фонда, сайт, представитель и должность, стадии, отрасли, регионы, чек из фонда, lead/follow, стадия/срок фонда, заявка/pitch deck |
| `micro_vc` | 🌱 Pre-seed / Seed фонд | VC subtype с маленькими/ранними раундами, stage and first-cheque mandate; не задавать фиксированные суммы по типу |
| `cvc` | 🏢 Корпоративный венчурный фонд (CVC) | Родительская компания, фонд/подразделение, стратегические темы, direct equity vs пилоты/поставщики, кто реально принимает решения |
| `family_office` | 💎 Family Office | single/multi family (где уместно), прямые сделки vs LP allocation/через фонды, мандат и контакт ответственного лица; не считать каждое family office прямым инвестором |
| `venture_studio` | 🧪 Venture Studio | co-building, доля/ресурсы, совместная команда, собственные инвестиции (да/нет); направление работы |
| `accelerator` | 🚀 Акселератор | Когорты/заявки, сроки, география, условия/доля/взнос, капитал (есть/нет), mentorship, demo day |
| `incubator` | 🌿 Инкубатор | Инфраструктура/менторство, условия набора, капитал (если есть), stage |
| `corporate_partner` | 🏭 Корпоративный партнёр | Пилоты, закупки, технология, PoC, нет обещания инвестиций без отдельного мандата CVC |
| `public_fund` | 🏛 Фонд развития / государственная поддержка | Грант/субсидия/заём/equity — только заявленная и подтверждённая форма, критерии программы, регион/сроки/статус набора |
| `impact_fund` | 🌍 Impact / тематический фонд | Юридическая форма VC/CVC/public + целевые сектора/impact-mandate, заявленные метрики; impact может быть **тегом стратегии**, не всегда отдельный `participant_type` |
| `other` | ➕ Другая организация | Категория по запросу с ручной модерацией, без auto-verified. |

**Типизация не должна плодить дубликаты одной сущности.** Например, один VC одновременно `seed` и `climate`: это *тип* VC + *инвестиционная стадия* seed + *сектор* climate. В интерфейсе небольшая, понятная классификация; доп. типы доступны через «Другой формат».

## 3. Общие обязательные поля анкеты для финансирующей стороны

1. **Кто вы?** Тип профиля (одно основное значение из управляемого каталога); можно обозначить параллельную стратегию, а не регистрировать два дубля.
2. **Публичное имя** ответственного человека и организация (если это фонд, клуб, акселератор/партнёр).
3. **Должность/полномочия** и доказуемое отношение к организации. Личный Telegram account обязателен технически, реальная личность/полномочия модератором оцениваются отдельно.
4. **Чем помогаете стартапам?** Multiselect support modes: прямые инвестиции, синдикат/co-invest, пилоты/покупки, гранты, mentorship/программа, партнёрство/создание проекта.
5. **Тематики** (1–8 multi-select) и **стадии** (>=1 multi-select), сохраняются в БД и применяются к ленте по умолчанию, как в Issue #2.
6. **Инвестиционный чек** — *только если есть прямое инвестирование*: min/max+currency или «обсуждаем индивидуально»; обязательно указать валюту, единицы хранения, отметить тип/инструмент (equity/SAFE/convertible/debt где юридически допустимо). У грантового оператора отдельные параметры гранта; у акселератора программы/условия.
7. **География** / локации регистрации стартапа и поддерживаемые юрисдикции.
8. **Сайт компании/фонда, профессиональная ссылка**, описание стратегии и способ связи.
9. **Как подать заявку:** через RAUNDO request + optional официальный внешний URL; принимает ли холодные заявки, открыт ли новый набор/инвестирование, примерные сроки ответа **только если источник подтвердил**.
10. Preview, согласия на обработку, права на публичную карточку, проектный статус `submitted` (нельзя auto-verify).

## 4. Разделение личности, организации и представителя

**Не хранить фонд только строкой `organization_name` у каждого инвестора.** Нужны сущности:
- `funding_organizations`: id, verified legal/public name, type, public domain, country, description, logo if authorized, status, owner/review/audit, support modes, open-to-requests.
- `organization_memberships`: user_id, organization_id, role (partner/analyst/program manager/lead), `claimed|under_review|approved|revoked`, verified_by/reason/at.
- `participant_profiles`: individual vs affiliated representative, type, investment strategy, cheque/stage/sectors/geography, reviewed status and explicit reviewed scope, linked org if approved.
- `organization_review_cases`, `membership_review_cases`, `participant_review_cases`: separate immutable revision history, evidence and actions; no verification inherited from a **self-declared** org name.
- `programs` optional for accelerator/grants with deadlines/eligibility; program funding and investor checks MUST stay distinct.

**Один фонд может иметь несколько подтверждённых сотрудников** с разграничением прав на заявки; один человек может состоять в нескольких организациях, если админ действительно проверил каждую связь. Использовать stable UUID / verified membership; не выводить чужих сотрудников из Telegram username. При увольнении админ отзывает membership без удаления истории/функций фонда. Нельзя получить права управления чужой организацией, подставив `organization_id` в callback.

## 5. Проверка в твоей /admin

Отдельная от стартапов общая очередь **«Финансирование и партнёры»** с фильтром типов:
- «Бизнес-ангелы/частные»
- «Фонды/VC/CVC/Family Office»
- «Синдикаты/клубы»
- «Акселераторы/инкубаторы/venture studios»
- «Корпоративные партнёры/гос. программы»

Для каждого кейса admin проверяет: 1) user Telegram ID (technically); 2) заполненность поля type/strategy/mandate; 3) публичные ссылки; 4) право представлять *эту* организацию (например официальный сайт/домен/публичный профиль/официальная заявка) при наличии; 5) указаны ли direct investment vs grant/program/cooperation, не вводит ли badge в заблуждение. Решения `approved|changes_requested|rejected|suspended` **с причиной и аудитом**, возможность запроса дополнительных подтверждений и апелляции. Избыточные паспорта, доказательства состоятельности и приватные инвест-документы не требуются по умолчанию. RAUNDO **не проверяет автоматически наличие капитала**, инвестиционную пригодность, аккредитацию по национальному праву или добросовестность.

Badges:
- `✅ Профиль бизнес-ангела проверен RAUNDO` — подтверждены заявленные сведения в рамках модерации, не гарантируется активный чек.
- `✅ Фонд / организация проверена RAUNDO` и независимо `✅ Представитель организации подтверждён`.
- `✅ Программа/акселератор рассмотрены RAUNDO` только с проверенным scope.
- **Никогда**: Telegram blue check, «капитал гарантирован», «официально аккредитованный инвестор» без проверки соответствующего правового статуса.

## 6. Лента, запросы, рейтинги и контакты

- Founder chooses who seeks: private angels, VC, micro VC, CVC, angel groups/syndicates, family offices, public funds, accelerators, corporate pilots; can select several. Additional filter funding type/amount/stage/geo and `support_modes`. Bot matches *both ways*: investor profiles → relevant startups; startups → relevant financing/partner profiles (opt-in).
- Representative of VC sees projects within **their own organization's mandate** if joining approved organization; can additionally set personal feed filters; show where preference comes from. Unverified individual sees only ordinary community features, not verified status.
- Contact request `intent` must match type: angel → personal investment, VC → fund introduction, accelerator → apply to program, CVC → strategic pilot or investment, public fund → program inquiry, syndicate → group/lead intro. Investor/fund claims and `open_for_requests` status shown. No unsolicited contact unlock on ❤️.
- **«Выбор инвесторов» 14-day ranking**: include a verified **human voting actor** with approved direct-investment mandate (angel, authorized VC/fund rep, direct family office, actual direct-invest CVC, syndicate lead *when mandate is confirmed*). Each human account gets **one eligible vote per startup per period**, not multiplied by number of organizations. Accelerators/grant programs/mentors without direct-investment capability are treated as community, not investor cohort. Manual verified org affiliation does not automatically prove investing activity.
- No paid badge, no pay-to-verify, no selling investor contact, no paid votes or rank; Investor PRO may serve varied organization types only with justified features; business rates to be validated.

## 7. Scope for Cursor currently working on PR #11

**Do not interrupt or overwrite current branch or change existing live structures unilaterally.** Existing `investor_type` string and investor form already include `angel`, `private`, `fund`, `family_office`, `corporate`, `accelerator`. Treat them as **legacy codes**. Expand selectively with explicit migration/compatibility mapping (`fund → vc_fund`, `corporate → cvc or corporate_partner depending verified direct investing`; `angel` unchanged). Do not auto-transform old corporate/accelerator members into verified investor cohort. For more complex organization memberships, plan schema change as atomic/small migrations, not just an unvalidated free-text role dropdown.

**Issue #2**: at minimum type-aware profile wizard and correct type/state/required fields/branching and persisted `support_modes`/mandate, test each category. **Issue #8**: entity verification, representative affiliation and review queues, separation direct-invest mandate from support org. **Issue #3**: two-way matching, relevant action CTA and contact consent, and correct investor leaderboard eligibility (Issue #4). Keep a single full product release, not a lite/full split.

## 8. Source references (classification only, no speculative revenue)

- Crunchbase investor categories: https://support.crunchbase.com/hc/en-us/articles/115010624128-Glossary-of-Investor-Types
- Carta types incl VC, angels, syndicates, accelerators, venture studio: https://carta.com/learn/startups/fundraising/investors/
- Angel Capital Association FAQ: https://angelcapitalassociation.org/faqs-angel-invest/
- PwC family offices 2026: https://www.pwc.com/gx/en/services/family-business/family-office/the-new-rules-of-family-office-investing.html
- NL Government stage/sector VC selection: https://business.gov.nl/finance-and-taxes/arrange-financing/funding-from-venture-capital-companies/
- PwC angel/VC/CVC distinctions: https://www.pwc.be/en/services/small-medium-or-family-business-for-the-future/start-ups-scale-ups/venture-deals/where-can-startups-find-money.html

These sources support the categories and distinctions only; they do NOT prove RAUNDO user demand, capital availability, average returns or willingness-to-pay.
