export type Analytics = {
  users_total: number;
  startups_total: number;
  startups_by_state: { label: string; value: number }[];
  investors_verified: number;
  investors_pending: number;
  organizations_total: number;
  organizations_verified: number;
  pending_reviews: number;
  open_tickets: number;
  awaiting_staff_tickets: number;
  awaiting_confirmation_tickets: number;
  resolved_tickets: number;
  closed_tickets: number;
  active_premium: number;
  open_complaints: number;
  series: { label: string; value: number }[];
};

function Bar({ label, value, max }: { label: string; value: number; max: number }) {
  const width = max > 0 ? Math.max(4, Math.round((value / max) * 100)) : 0;
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="muted small">{label}</span>
        <strong>{value}</strong>
      </div>
      <div
        style={{
          height: 8,
          borderRadius: 999,
          background: "var(--bg-subtle)",
          marginTop: 4,
          overflow: "hidden",
        }}
      >
        <div style={{ width: `${width}%`, height: "100%", background: "var(--accent)" }} />
      </div>
    </div>
  );
}

export function AnalyticsPage({ data }: { data: Analytics | null }) {
  if (!data) return <p className="muted">Загрузка аналитики…</p>;
  const max = Math.max(1, ...data.startups_by_state.map((s) => s.value));
  return (
    <section className="stack">
      <div className="page-head">
        <div>
          <h1>Аналитика</h1>
          <p className="page-lead">
            Только реальные агрегаты из базы. Никаких выдуманных трендов или «средних по рынку».
          </p>
        </div>
      </div>
      <div className="cards">
        {data.series.map((item) => (
          <div key={item.label} className="card">
            <span className="card-label">{item.label}</span>
            <span className="card-value">{item.value}</span>
          </div>
        ))}
        <div className="card">
          <span className="card-label">Ждут сотрудника</span>
          <span className="card-value">{data.awaiting_staff_tickets}</span>
        </div>
        <div className="card">
          <span className="card-label">Ждут подтверждения</span>
          <span className="card-value">{data.awaiting_confirmation_tickets}</span>
        </div>
        <div className="card">
          <span className="card-label">Решённые обращения</span>
          <span className="card-value">{data.resolved_tickets}</span>
        </div>
        <div className="card">
          <span className="card-label">Закрытые обращения</span>
          <span className="card-value">{data.closed_tickets}</span>
        </div>
        <div className="card">
          <span className="card-label">Активные Premium</span>
          <span className="card-value">{data.active_premium}</span>
        </div>
      </div>
      <div className="split">
        <div className="panel stack">
          <h2>Стартапы по статусам</h2>
          {data.startups_by_state.map((item) => (
            <Bar key={item.label} label={item.label} value={item.value} max={max} />
          ))}
        </div>
        <div className="panel stack">
          <h2>Профили и организации</h2>
          <div className="field-grid">
            <div>
              <div className="muted field-key">Подтверждённые инвесторы</div>
              <strong>{data.investors_verified}</strong>
            </div>
            <div>
              <div className="muted field-key">Инвесторы на проверке</div>
              <strong>{data.investors_pending}</strong>
            </div>
            <div>
              <div className="muted field-key">Организации всего</div>
              <strong>{data.organizations_total}</strong>
            </div>
            <div>
              <div className="muted field-key">Организации подтверждены</div>
              <strong>{data.organizations_verified}</strong>
            </div>
            <div>
              <div className="muted field-key">Стартапы на проверке</div>
              <strong>{data.pending_reviews}</strong>
            </div>
            <div>
              <div className="muted field-key">Открытые жалобы</div>
              <strong>{data.open_complaints}</strong>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
