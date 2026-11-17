import logo from "../checkout/logo.png";

const BOT_URL = "https://t.me/RaundoAppBot";

type Duration = {
  code: string;
  title: string;
  price_minor: number | null;
  currency: string;
  approved: boolean;
};

type Plan = {
  code: string;
  title: string;
  audience: string;
  durations: Duration[];
  benefits: string[];
};

const fallbackPlans: Plan[] = [
  {
    code: "community_plus",
    title: "Community PLUS",
    audience: "Для зрителей и участников сообщества",
    durations: [],
    benefits: ["Расширенные коллекции избранного", "История просмотренных проектов"],
  },
  {
    code: "startup_pro",
    title: "Startup PRO",
    audience: "Для основателей стартапов",
    durations: [],
    benefits: ["До 3 стартапов вместо 1", "До 3 фотографий в карточке"],
  },
  {
    code: "investor_pro",
    title: "Investor PRO",
    audience: "Для инвесторов и фондов",
    durations: [],
    benefits: ["До 3 инвесторских профилей", "Сохранённые стратегии поиска"],
  },
  {
    code: "organization_pro",
    title: "Organization PRO",
    audience: "Для фондов и организаций",
    durations: [],
    benefits: ["До 3 организаций", "До 5 представителей"],
  },
];

function formatPrice(minor: number | null, currency: string): string {
  if (minor === null) return "на утверждении";
  const amount = minor / 100;
  const text = Number.isInteger(amount)
    ? amount.toLocaleString("ru-RU")
    : amount.toLocaleString("ru-RU", { minimumFractionDigits: 2 });
  return currency === "RUB" ? `${text} ₽` : `${text} ${currency}`;
}

export async function loadPlans(): Promise<Plan[]> {
  try {
    const res = await fetch("/api/public/premium-plans", {
      headers: { accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return fallbackPlans;
    const data = (await res.json()) as { plans?: Plan[] };
    return data.plans && data.plans.length ? data.plans : fallbackPlans;
  } catch {
    return fallbackPlans;
  }
}

export function LandingPage({ plans }: { plans: Plan[] }) {
  return (
    <main className="landing">
      <div className="landing__glow" aria-hidden="true" />

      <section className="landing__hero">
        <img className="landing__logo" src={logo} alt="RAUNDO" width={112} height={112} />
        <h1 className="landing__title">RAUNDO</h1>
        <p className="landing__lead">
          Платформа, где стартапы встречают инвесторов, а участники рынка знакомятся и
          договариваются о сотрудничестве.
        </p>
        <div className="landing__actions">
          <a className="landing__cta" href={BOT_URL} target="_blank" rel="noopener noreferrer">
            Открыть RAUNDO в Telegram
          </a>
          <a className="landing__ghost" href="/premium">
            Тарифы Premium
          </a>
        </div>
        <p className="landing__note">
          RAUNDO не принимает инвестиции, не является брокером и не гарантирует финансирование.
        </p>
      </section>

      <section className="landing__section" aria-labelledby="what">
        <h2 id="what">Что внутри</h2>
        <div className="landing__grid">
          <article className="landing__card">
            <h3>Стартапы</h3>
            <p>Анкеты с описанием продукта, стадии и запроса. Публикация после модерации.</p>
          </article>
          <article className="landing__card">
            <h3>Инвесторы</h3>
            <p>Профили с этапами, отраслями и чеком. Контакты открываются после принятого запроса.</p>
          </article>
          <article className="landing__card">
            <h3>Организации</h3>
            <p>Фонды, студии и корпоративные партнёрства с подтверждением представителей.</p>
          </article>
        </div>
      </section>

      <section className="landing__section" aria-labelledby="premium">
        <h2 id="premium">Premium</h2>
        <p className="landing__section-lead">
          Платная часть — цифровые возможности платформы: расширенные лимиты анкет, коллекции и
          инструменты поиска. Это плата за функциональность RAUNDO, а не инвестиция.
        </p>
        <div className="landing__plans">
          {plans.map((plan) => {
            const cheapest = plan.durations
              .filter((d) => d.approved && d.price_minor !== null)
              .sort((a, b) => (a.price_minor ?? 0) - (b.price_minor ?? 0))[0];
            return (
              <article className="landing__plan" key={plan.code}>
                <h3>{plan.title}</h3>
                <p className="landing__plan-audience">{plan.audience}</p>
                <p className="landing__plan-price">
                  {cheapest ? `от ${formatPrice(cheapest.price_minor, cheapest.currency)}` : "цена на утверждении"}
                </p>
                <ul>
                  {plan.benefits.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </article>
            );
          })}
        </div>
        <p className="landing__note">
          Срок: 3, 7 или 30 дней. Автопродление не подключено: покупка разовая, продление — по
          желанию. Оплата в боте открывается после согласования Telegram Stars; карта и СБП внутри
          бота пока не включены. Реальные списания сейчас отключены.
        </p>
        <a className="landing__ghost" href="/premium">
          Подробные условия и цены
        </a>
      </section>

      <footer className="landing__footer-block">
        <div className="landing__legal">
          <p>Портфолио-версия RAUNDO. Персональные и юридические реквизиты не включены в открытые исходники.</p>
          <p><a href={BOT_URL} target="_blank" rel="noopener noreferrer">Поддержка RAUNDO в Telegram</a></p>
        </div>
        <nav className="landing__links" aria-label="Документы">
          <a href="/legal">Документы</a>
          <a href="/legal/terms">Соглашение</a>
          <a href="/legal/offer">Оферта</a>
          <a href="/legal/privacy">Политика</a>
          <a href="/legal/payments">Оплата и возвраты</a>
          <a href="/legal/requisites">Реквизиты</a>
        </nav>
      </footer>
    </main>
  );
}
