export type Overview = {
  users: number;
  published_startups: number;
  verified_investors: number;
  pending_startups: number;
  pending_investors: number;
  pending_organizations: number;
  pending_representatives: number;
  open_tickets: number;
  tickets_awaiting_staff: number;
  tickets_awaiting_user: number;
  tickets_awaiting_confirmation: number;
  tickets_needs_help: number;
  tickets_resolved: number;
  open_complaints: number;
  active_premium: number;
  role: string;
};

type Card = { label: string; value: number; hint?: string; section: string };

export function OverviewPage({
  data,
  role,
  onNavigate,
}: {
  data: Overview | null;
  role: string;
  onNavigate: (section: string) => void;
}) {
  if (!data) return <p className="muted">Загрузка обзора…</p>;
  const canMod = ["owner", "administrator", "moderator"].includes(role);
  const canSupport = ["owner", "administrator", "support"].includes(role);
  const canAdmin = ["owner", "administrator"].includes(role);
  const canOwner = role === "owner";
  const allowed = (section: string): boolean => {
    if (section === "moderation") return canMod;
    if (section === "support" || section === "users" || section === "complaints") return canSupport || canMod;
    if (section === "orgs") return canMod || canSupport;
    if (section === "premium" || section === "audit") return canAdmin;
    if (section === "staff") return canOwner;
    return true;
  };
  const queue = data.pending_startups + data.pending_investors;
  const orgQueue = data.pending_organizations + data.pending_representatives;
  const cards: Card[] = [
    {
      label: "Очередь модерации",
      value: queue,
      hint: "стартапы и инвесторы ждут решения",
      section: "moderation",
    },
    {
      label: "Проверка организаций",
      value: orgQueue,
      hint: "организации и представители ждут проверки",
      section: "orgs",
    },
    { label: "Обращения в поддержку", value: data.open_tickets, hint: "активные: новые, в работе, ждут ответа", section: "support" },
    {
      label: "Ждут сотрудника",
      value: data.tickets_awaiting_staff,
      hint: "новые и в работе",
      section: "support",
    },
    {
      label: "Ждут подтверждения",
      value: data.tickets_awaiting_confirmation,
      hint: "ответ отправлен, пользователь проверяет",
      section: "support",
    },
    {
      label: "Требуется помощь",
      value: data.tickets_needs_help,
      hint: "пользователь сообщил, что вопрос не решён",
      section: "support",
    },
    {
      label: "Решённые обращения",
      value: data.tickets_resolved,
      hint: "завершены сотрудником",
      section: "support",
    },
    { label: "Открытые жалобы", value: data.open_complaints, hint: "открытые", section: "complaints" },
    { label: "Пользователи платформы", value: data.users, hint: "всего в боте", section: "users" },
    {
      label: "Опубликованные стартапы",
      value: data.published_startups,
      hint: "подтверждённые",
      section: "moderation",
    },
    {
      label: "Подтверждённые инвесторы",
      value: data.verified_investors,
      hint: "прошли ручную проверку",
      section: "moderation",
    },
    { label: "Активные Premium", value: data.active_premium, hint: "активные подписки", section: "premium" },
  ];
  return (
    <section className="stack">
      <div className="page-head">
        <div>
          <h1>Обзор</h1>
          <p className="page-lead">
            Сводка по модерации, поддержке и подтверждённым профилям. Все значения считаются на
            сервере из рабочей базы.
          </p>
        </div>
      </div>
      <div className="cards">
        {cards.map((card) => (
          <button
            key={card.label}
            type="button"
            className={allowed(card.section) ? "card clickable" : "card"}
            disabled={!allowed(card.section)}
            onClick={() => allowed(card.section) && onNavigate(card.section)}
          >
            <span className="card-label">{card.label}</span>
            <span className="card-value">{card.value}</span>
            {card.hint && <span className="card-hint">{card.hint}</span>}
          </button>
        ))}
      </div>
      {queue > 0 && canMod && (
        <div className="panel row">
          <strong>{queue}</strong>
          <span className="muted">анкет ждут решения. Откройте модерацию, чтобы не держать очередь.</span>
          <button type="button" className="primary" onClick={() => onNavigate("moderation")}>
            Перейти к модерации
          </button>
        </div>
      )}
      {orgQueue > 0 && (canMod || canSupport) && (
        <div className="panel row">
          <strong>{orgQueue}</strong>
          <span className="muted">организаций и представителей ждут проверки.</span>
          <button type="button" className="primary" onClick={() => onNavigate("orgs")}>
            Открыть организации
          </button>
        </div>
      )}
    </section>
  );
}
