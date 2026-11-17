import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

type Item = {
  kind: string;
  id: string;
  title: string;
  subtitle: string;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  owner: string;
  username: string | null;
  telegram_id: number | null;
  premium: boolean;
  cover_url?: string;
  amount_label?: string;
};
type Counts = Record<string, { total: number; statuses: Record<string, number> }>;
type Payload = { items: Item[]; total: number; page: number; page_size: number; counts: Counts };
type Photo = { position: number; kind?: string; url: string };
type Review = Record<string, unknown>;
type Detail = {
  kind: string;
  status: string;
  item: Record<string, unknown>;
  owner: Record<string, unknown> | null;
  premium: { plan: string; owner_type: string; expires_at: string | null }[];
  reviews: Review[];
  photos: Photo[];
  review_id: string | null;
};

const PHOTO_PLACEHOLDER =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='100'%3E%3Crect width='140' height='100' fill='%23f2f2f2'/%3E%3C/svg%3E";

const KIND_LABEL: Record<string, string> = {
  startup: "Стартап",
  investor: "Инвестор",
  fund: "Фонд",
  organization: "Организация",
};
const STATUS_LABEL: Record<string, string> = {
  draft: "Черновик",
  submitted: "На модерации",
  pending: "На модерации",
  approved: "Опубликована",
  rejected: "Отклонена",
  changes_requested: "На исправлении",
  archived: "Архив",
};
const FIELD_LABEL: Record<string, string> = {
  name: "Название",
  display_name: "Имя",
  one_liner: "Кратко",
  full_description: "Описание",
  description: "Описание",
  strategy_description: "Стратегия",
  website_url: "Сайт",
  app_url: "Приложение",
  demo_url: "Демо",
  country: "Страна",
  founder_display_name: "Основатель",
  founder_role: "Роль основателя",
  ask_amount_minor: "Запрос, коп.",
  ask_currency: "Валюта запроса",
  min_check_minor: "Чек от, коп.",
  max_check_minor: "Чек до, коп.",
  currency: "Валюта",
  investor_type: "Тип инвестора",
  organization_name: "Организация",
  participant_type: "Тип участника",
  contact_policy: "Политика контактов",
  contact_username: "Контакт",
  created_at: "Создана",
  updated_at: "Обновлена",
  published_at: "Опубликована",
  approved_at: "Одобрена",
  deleted_at: "Удалена",
  review_state: "Статус",
  review_status: "Статус",
};
const HIDDEN_FIELDS = new Set([
  "id",
  "owner_user_id",
  "user_id",
  "created_by_user_id",
  "published_snapshot",
  "grandfathered_gallery",
]);

function statusClass(status: string) {
  if (status === "approved") return "badge badge--ok";
  if (status === "rejected") return "badge badge--danger";
  if (status === "archived") return "badge badge--muted";
  return "badge badge--warn";
}

function humanValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === "object" ? humanValue(item) : String(item)))
      .join(", ");
  }
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}: ${humanValue(item)}`)
      .join("; ");
  }
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}T/.test(text)) return new Date(text).toLocaleString("ru-RU");
  return text;
}


const SECTIONS: Record<string, { title: string; keys: string[] }[]> = {
  startup: [
    { title: "О проекте", keys: ["name", "one_liner", "full_description", "country"] },
    {
      title: "Инвестиции",
      keys: ["ask_amount_minor", "ask_currency", "fundraising_stage", "use_of_funds_codes", "use_of_funds_note"],
    },
    {
      title: "Бизнес",
      keys: ["monetization_status", "monetization_note", "progress_codes", "audience_codes", "audience_note", "intents"],
    },
    { title: "Команда", keys: ["founder_display_name", "founder_role"] },
    {
      title: "Контакты",
      keys: ["website_url", "app_url", "demo_url", "contact_policy", "contact_username", "no_link_reason"],
    },
  ],
  investor: [
    {
      title: "Инвестор",
      keys: ["display_name", "investor_type", "organization_name", "position", "strategy_description"],
    },
    {
      title: "Инвестиции",
      keys: ["min_check_minor", "max_check_minor", "currency", "check_discuss_individually", "seeking", "geography", "support_modes"],
    },
    { title: "Контакты", keys: ["public_reference_url", "contact_policy", "contact_username"] },
  ],
  organization: [
    { title: "Организация", keys: ["name", "participant_type", "description", "country", "website_url"] },
    { title: "Работа", keys: ["support_modes", "open_for_requests"] },
  ],
};

const TECH_FIELDS = new Set([
  "id", "slug", "owner_user_id", "user_id", "created_by_user_id", "primary_sector_id", "stage_id",
  "current_revision", "published_snapshot", "grandfathered_gallery", "review_state", "review_status",
  "created_at", "updated_at",
]);

function DetailBody({ detail }: { detail: Detail }) {
  const item = detail.item;
  const sections = SECTIONS[detail.kind] ?? [];
  const known = new Set(sections.flatMap((section) => section.keys));
  const technical = Object.entries(item).filter(
    ([key, value]) => !known.has(key) && !TECH_FIELDS.has(key) && value !== null && value !== "",
  );
  return (
    <>
      {sections.map((section) => {
        const entries = section.keys
          .map((key) => [key, item[key]] as const)
          .filter(([, value]) => value !== null && value !== "" && value !== undefined);
        if (entries.length === 0) return null;
        return (
          <div key={section.title} className="stack" style={{ gap: 6 }}>
            <h3>{section.title}</h3>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(160px, 220px) 1fr", gap: "6px 16px" }}>
              {entries.map(([key, value]) => (
                <div key={key} style={{ display: "contents" }}>
                  <div className="muted small">{FIELD_LABEL[key] || key.replaceAll("_", " ")}</div>
                  <div className="small" style={{ wordBreak: "break-word" }}>{humanValue(value)}</div>
                </div>
              ))}
            </div>
          </div>
        );
      })}
      <details>
        <summary className="muted small" style={{ cursor: "pointer" }}>Техническая информация</summary>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(160px, 220px) 1fr", gap: "6px 16px", marginTop: 8 }}>
          {Object.entries(item)
            .filter(([key, value]) => (TECH_FIELDS.has(key) || !known.has(key)) && value !== null && value !== undefined && value !== "")
            .map(([key, value]) => (
              <div key={key} style={{ display: "contents" }}>
                <div className="muted small">{key}</div>
                <div className="small" style={{ wordBreak: "break-word" }}>{humanValue(value)}</div>
              </div>
            ))}
        </div>
      </details>
      {technical.length === 0 ? null : null}
    </>
  );
}

export function CatalogPage({
  csrf,
  onNavigate,
}: {
  csrf: string;
  onNavigate: (section: string, focus?: string) => void;
}) {
  const [data, setData] = useState<Payload | null>(null);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [view, setView] = useState<"cards" | "table">("cards");
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (kind) params.set("type", kind);
    if (status) params.set("status", status);
    params.set("page", String(page));
    setError("");
    api<Payload>(`/catalog?${params.toString()}`, csrf)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Не удалось загрузить каталог."));
  }, [q, kind, status, page, csrf]);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  async function open(item: Item) {
    try {
      setDetail((await api(`/catalog/${item.kind}/${item.id}`, csrf)) as Detail);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось открыть анкету.");
    }
  }

  async function moderate(action: "approve" | "changes" | "reject") {
    if (!detail || !detail.review_id) return;
    let comment = "";
    if (action !== "approve") {
      const prompt = action === "reject" ? "Причина отклонения:" : "Что нужно исправить:";
      comment = window.prompt(prompt, "")?.trim() || "";
      if (comment.length < 8) {
        window.alert("Комментарий должен быть не короче 8 символов.");
        return;
      }
      if (!window.confirm("Отправить решение и уведомить пользователя?")) return;
    } else if (!window.confirm("Одобрить и опубликовать анкету?")) {
      return;
    }
    setBusy(true);
    try {
      const path = `/moderation/${detail.kind}s/${detail.review_id}/${action}`;
      await api(path, csrf, {
        method: "POST",
        body: action === "approve" ? undefined : JSON.stringify({ comment }),
      });
      setDetail((await api(`/catalog/${detail.kind}/${detail.item.id}`, csrf)) as Detail);
      setReloadKey((value) => value + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выполнить действие.");
    } finally {
      setBusy(false);
    }
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="stack">
      <div className="panel stack">
        <div className="panel-head">
          <h2>Каталог</h2>
          <div className="row-actions">
            <button type="button" className={view === "cards" ? "active" : ""} onClick={() => setView("cards")}>
              Карточки
            </button>
            <button type="button" className={view === "table" ? "active" : ""} onClick={() => setView("table")}>
              Таблица
            </button>
          </div>
        </div>
        <p className="muted">Все анкеты платформы: черновики, модерация, публикации и архив.</p>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {(["startup", "investor", "fund", "organization"] as const).map((key) => (
            <div key={key} className="panel" style={{ minWidth: 150, padding: 12, flex: "1 1 150px" }}>
              <div className="muted small">{KIND_LABEL[key]}</div>
              <div style={{ fontSize: 26, fontWeight: 600 }}>{data?.counts?.[key]?.total ?? 0}</div>
              <div className="muted small">
                опубликовано {data?.counts?.[key]?.statuses?.approved ?? 0} · модерация{" "}
                {data?.counts?.[key]?.statuses?.submitted ?? data?.counts?.[key]?.statuses?.pending ?? 0} ·
                черновики {data?.counts?.[key]?.statuses?.draft ?? 0}
              </div>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input
            placeholder="Поиск: название, @username, Telegram ID или UUID"
            value={q}
            onChange={(e) => {
              setPage(1);
              setQ(e.target.value);
            }}
            style={{ minWidth: 260, flex: 1 }}
          />
          <select value={kind} onChange={(e) => { setPage(1); setKind(e.target.value); }}>
            <option value="">Все типы</option>
            <option value="startup">Стартапы</option>
            <option value="investor">Инвесторы</option>
            <option value="fund">Фонды</option>
            <option value="organization">Организации</option>
          </select>
          <select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
            <option value="">Все статусы</option>
            {Object.entries(STATUS_LABEL).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
        {error && <p className="error-text">{error}</p>}
        {!data ? (
          <p className="muted">Загрузка каталога…</p>
        ) : data.items.length === 0 ? (
          <p className="muted">Ничего не найдено.</p>
        ) : view === "table" ? (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Название</th><th>Тип</th><th>Статус</th><th>Владелец</th><th>Premium</th><th>Обновлено</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={`${item.kind}:${item.id}`} onClick={() => void open(item)} style={{ cursor: "pointer" }}>
                    <td>
                      <strong>{item.title}</strong>
                      {item.subtitle && <div className="muted small">{item.subtitle}</div>}
                    </td>
                    <td>{KIND_LABEL[item.kind] || item.kind}</td>
                    <td><span className={statusClass(item.status)}>{STATUS_LABEL[item.status] || item.status}</span></td>
                    <td>
                      {item.owner}{item.username ? ` (@${item.username})` : ""}
                      {item.telegram_id ? <div className="muted small">{item.telegram_id}</div> : null}
                    </td>
                    <td>{item.premium ? "Да" : "Нет"}</td>
                    <td>{item.updated_at ? new Date(item.updated_at).toLocaleString("ru-RU") : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
            {data.items.map((item) => (
              <button
                key={`${item.kind}:${item.id}`}
                type="button"
                className="panel"
                onClick={() => void open(item)}
                style={{ textAlign: "left", cursor: "pointer", padding: 14, minHeight: 130, minWidth: 0, overflowWrap: "anywhere" }}
              >
                {item.cover_url ? (
                  <img
                    src={item.cover_url}
                    alt=""
                    loading="lazy"
                    onError={(event) => {
                      event.currentTarget.src = PHOTO_PLACEHOLDER;
                    }}
                    style={{ width: "100%", height: 140, objectFit: "cover", borderRadius: 10, marginBottom: 8 }}
                  />
                ) : null}
                <div className="muted small">{KIND_LABEL[item.kind] || item.kind}</div>
                <div style={{ fontSize: 17, fontWeight: 600, margin: "4px 0" }}>{item.title}</div>
                <div className="muted small" style={{ minHeight: 32 }}>{item.subtitle || "Без описания"}</div>
                {item.amount_label ? (
                  <div className="small" style={{ marginTop: 4 }}>{item.amount_label}</div>
                ) : null}
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 10, gap: 8 }}>
                  <span className={statusClass(item.status)}>{STATUS_LABEL[item.status] || item.status}</span>
                  <span className="muted small">{item.premium ? "Premium" : "Free"}</span>
                </div>
                <div className="muted small" style={{ marginTop: 6 }}>
                  {item.owner}{item.username ? ` (@${item.username})` : ""}
                </div>
              </button>
            ))}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button type="button" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>Назад</button>
          <span className="muted small">Страница {data?.page ?? 1} из {pages}</span>
          <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Вперёд</button>
          <span className="muted small">Всего: {data?.total ?? 0}</span>
        </div>
      </div>

      {detail && (
        <div className="panel stack">
          <div className="panel-head">
            <h2>{String(detail.item.name || detail.item.display_name || "Анкета")}</h2>
            <button type="button" onClick={() => setDetail(null)}>Закрыть</button>
          </div>
          <div className="muted small">
            {KIND_LABEL[detail.kind] || detail.kind} ·{" "}
            {STATUS_LABEL[detail.status] || detail.status}
          </div>
          {detail.photos.length > 0 && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {detail.photos.map((photo) => (
                <img
                  key={photo.url}
                  src={photo.url}
                  alt="Фото анкеты"
                  loading="lazy"
                  onError={(event) => {
                    event.currentTarget.src = PHOTO_PLACEHOLDER;
                  }}
                  style={{ width: 140, height: 100, objectFit: "cover", borderRadius: 12, background: "#f2f2f2" }}
                />
              ))}
            </div>
          )}
          <DetailBody detail={detail} />
          {detail.owner && (
            <div className="panel stack" style={{ padding: 12 }}>
              <div><strong>Владелец:</strong> {String(detail.owner.first_name || "")} {String(detail.owner.last_name || "")}</div>
              <div className="muted small">
                @{String(detail.owner.username || "-")} · Telegram ID {String(detail.owner.telegram_id || "-")}
              </div>
              <div>
                <button type="button" onClick={() => onNavigate("users", String(detail.owner?.id || ""))}>
                  Открыть владельца
                </button>
              </div>
            </div>
          )}
          {detail.premium.length > 0 && (
            <div className="small">
              <strong>Premium:</strong>{" "}
              {detail.premium.map((sub) => `${sub.plan} до ${sub.expires_at ? new Date(sub.expires_at).toLocaleDateString("ru-RU") : "-"}`).join("; ")}
            </div>
          )}
          <div className="row-actions" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {detail.kind === "organization" ? (
              <button type="button" onClick={() => onNavigate("orgs", String(detail.item.id))}>
                Открыть в разделе организаций
              </button>
            ) : detail.review_id && ["submitted", "pending", "changes_requested"].includes(detail.status) ? (
              <>
                <button type="button" disabled={busy} onClick={() => void moderate("approve")}>Одобрить</button>
                <button type="button" disabled={busy} onClick={() => void moderate("changes")}>Запросить исправления</button>
                <button type="button" disabled={busy} onClick={() => void moderate("reject")}>Отклонить</button>
              </>
            ) : (
              <span className="muted small">Нет активной заявки на модерацию.</span>
            )}
          </div>
          <h3>История модерации</h3>
          {detail.reviews.length === 0 ? (
            <p className="muted small">Истории пока нет.</p>
          ) : (
            <div className="stack">
              {detail.reviews.map((review, index) => (
                <div key={String(review.id || index)} className="panel" style={{ padding: 12 }}>
                  <div className="small">
                    <strong>{STATUS_LABEL[String(review.state)] || String(review.state)}</strong>
                    {" · "}
                    {review.submitted_at ? new Date(String(review.submitted_at)).toLocaleString("ru-RU") : "-"}
                    {review.decided_at ? ` → ${new Date(String(review.decided_at)).toLocaleString("ru-RU")}` : ""}
                  </div>
                  {review.comment ? <div className="muted small">{String(review.comment)}</div> : null}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
