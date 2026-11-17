import { useCallback, useEffect, useRef, useState } from "react";
import { api, statusBadgeClass, statusLabel } from "../api/client";
import type { AuthState } from "../api/client";
import { DataTable } from "../components/DataTable";

type ComplaintRow = {
  id: string;
  number: string;
  target_type: string;
  target_id: string;
  target_title: string | null;
  reason: string;
  status: string;
  priority: string;
  created_at: string | null;
  has_evidence: boolean;
};

type ComplaintList = {
  items: ComplaintRow[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  statuses: string[];
  target_types: Record<string, string>;
  status_labels: Record<string, string>;
  priorities: string[];
  can_decide: boolean;
};

type Target = {
  type: string;
  id: string;
  title: string;
  subtitle: string;
  section: string;
  status: string;
} | null;

type ComplaintDetail = {
  id: string;
  number: string;
  target_type: string;
  target_type_label: string;
  target_id: string;
  target: Target;
  reason: string;
  status: string;
  priority: string;
  created_at: string | null;
  resolution_comment: string | null;
  resolved_at: string | null;
  resolution_author: string | null;
  reporter: {
    id: string | null;
    telegram_id: number | null;
    username: string | null;
    name: string | null;
    status: string | null;
    restrictions: { id: string; reason: string; active: boolean; created_at: string | null }[];
  };
  evidence: { kind: string | null; name: string | null; size: number | null } | null;
  history: {
    action: string;
    actor: string | null;
    reason: string | null;
    before_state: Record<string, unknown> | null;
    after_state: Record<string, unknown> | null;
    created_at: string;
  }[];
  allowed_statuses: string[];
  can_restrict_target: boolean;
  can_decide: boolean;
  status_labels: Record<string, string>;
};

type Filters = { q: string; status: string; targetType: string; page: number };

const STORAGE_KEY = "raundo-admin-complaints-view";

const DEFAULT_FILTERS: Filters = { q: "", status: "", targetType: "", page: 1 };

const PRIORITY_LABELS: Record<string, string> = {
  low: "Низкий",
  normal: "Обычный",
  high: "Высокий",
};

const FILTER_ORDER = ["open", "in_review", "resolved", "dismissed"];

const DECISION_LABELS: Record<string, string> = {
  in_review: "Принять в работу",
  resolved: "Рассмотреть",
  dismissed: "Закрыть",
  open: "Вернуть в новые",
};

function decisionLabel(status: string, current: string, labels: Record<string, string>): string {
  if (status === "in_review" && (current === "resolved" || current === "dismissed")) {
    return "Вернуть в работу";
  }
  return DECISION_LABELS[status] || labels[status] || statusLabel(status);
}

function readFilters(): Filters {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_FILTERS;
    const parsed = JSON.parse(raw) as Partial<Filters>;
    return {
      q: typeof parsed.q === "string" ? parsed.q : "",
      status: typeof parsed.status === "string" ? parsed.status : "",
      targetType: typeof parsed.targetType === "string" ? parsed.targetType : "",
      page: typeof parsed.page === "number" && parsed.page > 0 ? parsed.page : 1,
    };
  } catch {
    return DEFAULT_FILTERS;
  }
}

function formatDateTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("ru-RU") : "—";
}

function formatBytes(size: number | null): string {
  if (!size) return "";
  if (size < 1024) return `${size} Б`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} КБ`;
  return `${(size / (1024 * 1024)).toFixed(1)} МБ`;
}

function priorityClass(priority: string): string {
  if (priority === "high") return "badge bad";
  if (priority === "low") return "badge";
  return "badge warn";
}

export function ComplaintsPage({
  me,
  focusId,
  onNavigate,
  onChanged,
}: {
  me: AuthState;
  focusId?: string;
  onNavigate: (section: string, focusId?: string) => void;
  onChanged?: () => void;
}) {
  const [filters, setFilters] = useState<Filters>(readFilters);
  const [data, setData] = useState<ComplaintList | null>(null);
  const [detail, setDetail] = useState<ComplaintDetail | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [decisionStatus, setDecisionStatus] = useState("in_review");
  const [comment, setComment] = useState("");
  const [restrictTarget, setRestrictTarget] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const requestRef = useRef(0);
  const detailRef = useRef<HTMLDivElement | null>(null);

  const scrollToDetail = useCallback(() => {
    const node = detailRef.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    if (rect.top > window.innerHeight - 120) {
      node.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, []);

  const load = useCallback(async () => {
    const request = ++requestRef.current;
    const params = new URLSearchParams({ page: String(filters.page), page_size: "25" });
    if (filters.q.trim()) params.set("q", filters.q.trim());
    if (filters.status) params.set("status", filters.status);
    if (filters.targetType) params.set("target_type", filters.targetType);
    try {
      const payload = await api<ComplaintList>(`/complaints?${params.toString()}`, me.csrf);
      if (request !== requestRef.current) return;
      setData(payload);
    } catch (e) {
      if (request === requestRef.current) {
        setError(e instanceof Error ? e.message : "Не удалось загрузить жалобы.");
      }
    }
  }, [filters, me.csrf]);

  const open = useCallback(
    async (id: string) => {
      setError("");
      setNotice("");
      try {
        const payload = await api<ComplaintDetail>(`/complaints/${id}`, me.csrf);
        setDetail(payload);
        // Never preselect a final status: the default must be a working state.
        const preferred = ["in_review", "open", "resolved", "dismissed"];
        setDecisionStatus(
          preferred.find((status) => payload.allowed_statuses.includes(status)) ||
            payload.allowed_statuses[0] ||
            payload.status,
        );
        setComment("");
        setRestrictTarget(false);
        setConfirm(false);
        window.setTimeout(scrollToDetail, 0);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Жалоба недоступна.");
        setDetail(null);
      }
    },
    [me.csrf, scrollToDetail],
  );

  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(filters));
    } catch {
      /* storage disabled */
    }
    const timer = window.setTimeout(() => void load(), filters.q.trim() ? 280 : 0);
    return () => window.clearTimeout(timer);
  }, [filters, load]);

  useEffect(() => {
    if (focusId) void open(focusId);
  }, [focusId, open]);

  function patchFilters(patch: Partial<Filters>) {
    setFilters((current) => ({ ...current, ...patch, page: patch.page ?? 1 }));
  }

  async function decide() {
    if (!detail) return;
    const isFinal = decisionStatus === "resolved" || decisionStatus === "dismissed";
    if (isFinal && comment.trim().length < 8) {
      setError("Для решения или закрытия нужен комментарий не короче 8 символов.");
      return;
    }
    if ((isFinal || restrictTarget) && !confirm) {
      setError("Подтвердите решение перед сохранением.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api<{ status: string; restricted?: boolean }>(
        `/complaints/${detail.id}`,
        me.csrf,
        {
          method: "PATCH",
          body: JSON.stringify({
            status: decisionStatus,
            comment: comment.trim() || undefined,
            restrict_target: restrictTarget && decisionStatus === "resolved",
          }),
        },
      );
      await open(detail.id);
      await load();
      onChanged?.();
      setNotice(
        result.restricted
          ? "Решение сохранено, доступ к объекту жалобы ограничен. Уведомление поставлено в очередь."
          : "Решение сохранено и записано в журнал.",
      );
    } catch (e) {
      // A stale card must be refreshed: another admin may have changed the status.
      try {
        setDetail(await api<ComplaintDetail>(`/complaints/${detail.id}`, me.csrf));
      } catch {
        /* keep the current card */
      }
      setError(e instanceof Error ? e.message : "Решение не сохранено.");
    } finally {
      setBusy(false);
    }
  }

  const statusLabels = data?.status_labels || {
    open: "Новая",
    in_review: "В работе",
    resolved: "Рассмотрена",
    dismissed: "Закрыта",
  };
  const targetTypes = data?.target_types || {};
  const isFinalDecision = decisionStatus === "resolved" || decisionStatus === "dismissed";
  const needsConfirm = isFinalDecision || restrictTarget;

  return (
    <section className="stack">
      <div className="page-head">
        <div>
          <h1>Жалобы</h1>
          <p className="page-lead">
            Жалобы из Telegram. Каждое решение сохраняется с комментарием и попадает в журнал
            действий; пользователь получает уведомление в бот.
          </p>
        </div>
      </div>
      {error && <p className="error-text">{error}</p>}
      {notice && <p className="ok-text">{notice}</p>}

      <div className="panel stack">
        <div className="filter-bar">
          <input
            aria-label="Поиск жалоб"
            placeholder="Номер жалобы или текст"
            value={filters.q}
            onChange={(e) => patchFilters({ q: e.target.value })}
          />
          <select
            aria-label="Тип объекта"
            value={filters.targetType}
            onChange={(e) => patchFilters({ targetType: e.target.value })}
          >
            <option value="">Все объекты</option>
            {Object.entries(targetTypes).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="row">
          <button
            type="button"
            className={filters.status === "" ? "primary small" : "ghost small"}
            onClick={() => patchFilters({ status: "" })}
          >
            Все
          </button>
          {FILTER_ORDER.map((status) => (
            <button
              key={status}
              type="button"
              className={filters.status === status ? "primary small" : "ghost small"}
              onClick={() => patchFilters({ status })}
            >
              {statusLabels[status] || statusLabel(status)}
            </button>
          ))}
          <button
            type="button"
            className="ghost small"
            onClick={() => {
              setFilters(DEFAULT_FILTERS);
            }}
          >
            Сбросить
          </button>
        </div>
        <p className="muted small">
          Найдено: {data?.total ?? 0} · страница {data?.page ?? 1} из {data?.pages ?? 1}
        </p>
      </div>

      <div className="stack">
        <div className="stack">
          <DataTable
            columns={[
              { key: "number", label: "Номер" },
              {
                key: "target_type",
                label: "Тип объекта",
                hideOnMobile: true,
                render: (row) => targetTypes[String(row.target_type)] || String(row.target_type),
              },
              {
                key: "target_title",
                label: "Объект",
                render: (row) => String(row.target_title || "—"),
              },
              {
                key: "reason",
                label: "Причина",
                hideOnMobile: true,
                render: (row) => {
                  const text = String(row.reason || "");
                  return <span title={text}>{text.length > 70 ? `${text.slice(0, 70)}…` : text}</span>;
                },
              },
              {
                key: "created_at",
                label: "Дата",
                hideOnMobile: true,
                render: (row) => formatDateTime(row.created_at as string | null),
              },
              {
                key: "status",
                label: "Статус",
                render: (row) => (
                  <span className={statusBadgeClass(String(row.status))}>
                    {statusLabels[String(row.status)] || statusLabel(String(row.status))}
                  </span>
                ),
              },
              {
                key: "priority",
                label: "Приоритет",
                render: (row) => (
                  <span className={priorityClass(String(row.priority))}>
                    {PRIORITY_LABELS[String(row.priority)] || String(row.priority)}
                  </span>
                ),
              },
            ]}
            rows={(data?.items || []) as unknown as Record<string, unknown>[]}
            onRowClick={(row) => void open(String(row.id))}
            rowKey={(row) => String(row.id)}
            actions={(row) => (
              <button
                type="button"
                className="ghost small"
                onClick={() => void open(String(row.id))}
              >
                Открыть
              </button>
            )}
            emptyText="Жалоб нет. Новые обращения появятся здесь автоматически."
          />
          <div className="pager">
            <button
              type="button"
              className="ghost small"
              disabled={!data || data.page <= 1}
              onClick={() => patchFilters({ page: Math.max(1, (data?.page ?? 1) - 1) })}
            >
              Назад
            </button>
            <span className="muted small">
              {data?.page ?? 1} / {data?.pages ?? 1}
            </span>
            <button
              type="button"
              className="ghost small"
              disabled={!data || data.page >= data.pages}
              onClick={() => patchFilters({ page: (data?.page ?? 1) + 1 })}
            >
              Вперёд
            </button>
          </div>
        </div>

        <div className={detail || focusId ? "panel stack" : "muted small"} ref={detailRef}>
          {!detail ? (
            <p className="muted">
              {focusId ? "Загрузка жалобы…" : "Выберите жалобу в списке."}
            </p>
          ) : (
            <>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <h2>{detail.number}</h2>
                  <p className="muted small">
                    {detail.target_type_label} · поступила {formatDateTime(detail.created_at)}
                  </p>
                </div>
                <div className="row">
                  <span className={priorityClass(detail.priority)}>
                    {PRIORITY_LABELS[detail.priority] || detail.priority}
                  </span>
                  <span className={statusBadgeClass(detail.status)}>
                    {detail.status_labels[detail.status] || statusLabel(detail.status)}
                  </span>
                </div>
              </div>

              <div>
                <h3>Содержание жалобы</h3>
                <p className="reason-text">{detail.reason}</p>
              </div>

              <div>
                <h3>Объект жалобы</h3>
                {detail.target ? (
                  <div className="linked-list" data-testid="complaint-target">
                    <div className="linked-row">
                      <div>
                        <strong>{detail.target.title}</strong>
                        <div className="muted small">{detail.target.subtitle}</div>
                      </div>
                      {detail.target.section && (
                        <button
                          type="button"
                          className="ghost small"
                          onClick={() =>
                            detail.target
                              ? onNavigate(
                                  detail.target.section,
                                  ["user", "organization"].includes(detail.target.type)
                                    ? detail.target.id
                                    : undefined,
                                )
                              : undefined
                          }
                        >
                          {detail.target.type === "user"
                            ? "Открыть профиль"
                            : detail.target.section === "moderation"
                              ? "К модерации"
                              : "Открыть"}
                        </button>
                      )}
                    </div>
                  </div>
                ) : (
                  <p className="muted small">
                    Объект не привязан к карточке: жалоба создана без ссылки на опубликованный
                    объект.
                  </p>
                )}
              </div>

              <div>
                <h3>Заявитель</h3>
                {detail.reporter.id ? (
                  <div className="linked-list" data-testid="complaint-reporter">
                    <div className="linked-row">
                      <div>
                        <strong>
                          {detail.reporter.name ||
                            (detail.reporter.username
                              ? `@${detail.reporter.username}`
                              : `ID ${detail.reporter.telegram_id}`)}
                        </strong>
                        <div className="muted small">
                          {detail.reporter.username ? `@${detail.reporter.username} · ` : ""}
                          Telegram ID {detail.reporter.telegram_id} ·{" "}
                          {statusLabel(detail.reporter.status || "active")}
                        </div>
                        {detail.reporter.restrictions.some((item) => item.active) && (
                          <div className="small muted">Есть активное ограничение доступа</div>
                        )}
                      </div>
                      <button
                        type="button"
                        className="ghost small"
                        onClick={() => onNavigate("users", detail.reporter.id || undefined)}
                      >
                        Открыть профиль
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="muted small">Заявитель не найден.</p>
                )}
              </div>

              <div>
                <h3>Доказательства</h3>
                {detail.evidence ? (
                  <div className="evidence-box">
                    <strong>
                      {detail.evidence.kind === "photo" ? "Фотография" : "Документ"}
                    </strong>
                    <div className="muted small">
                      {detail.evidence.name || "без имени файла"}
                      {detail.evidence.size
                        ? ` · ${formatBytes(detail.evidence.size)}`
                        : ""}
                    </div>
                    <p className="muted small">
                      Вложение хранится в Telegram. В веб-панели доступны только метаданные —
                      файл не скачивается и не публикуется.
                    </p>
                  </div>
                ) : (
                  <p className="muted small">Вложений нет.</p>
                )}
              </div>

              {detail.resolution_comment && (
                <div className="callout">
                  <strong>Сохранённое решение</strong>
                  <p>{detail.resolution_comment}</p>
                  <p className="muted small">
                    {detail.resolution_author || "сотрудник"} ·{" "}
                    {formatDateTime(detail.resolved_at)}
                  </p>
                </div>
              )}

              <div>
                <h3>История обработки</h3>
                {detail.history.length === 0 ? (
                  <p className="muted small">Решений ещё не было.</p>
                ) : (
                  <ul className="timeline">
                    {detail.history.map((entry, index) => (
                      <li key={`${entry.created_at}-${index}`}>
                        <span className="timeline-dot" aria-hidden="true" />
                        <div>
                          <strong>
                            {entry.after_state && typeof entry.after_state.status === "string"
                              ? detail.status_labels[entry.after_state.status] ||
                                statusLabel(entry.after_state.status)
                              : entry.action}
                          </strong>
                          <div className="muted small">
                            {entry.actor || "система"} · {formatDateTime(entry.created_at)}
                          </div>
                          {entry.reason && <div className="small">{entry.reason}</div>}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {detail.can_decide ? (
                <div className="decision-box">
                  <h3>Решение</h3>
                  <div className="status-actions mt-12">
                    {detail.allowed_statuses.map((status) => (
                      <button
                        key={status}
                        type="button"
                        className={
                          decisionStatus === status ? "primary small" : "ghost small"
                        }
                        onClick={() => {
                          setDecisionStatus(status);
                          if (status !== "resolved") setRestrictTarget(false);
                        }}
                      >
                        {decisionLabel(status, detail.status, detail.status_labels)}
                      </button>
                    ))}
                  </div>
                  <textarea
                    className="mt-12"
                    aria-label="Комментарий к решению"
                    placeholder="Комментарий к решению (обязателен для «Рассмотреть» и «Закрыть»)"
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                  />
                  {detail.can_restrict_target && decisionStatus === "resolved" && (
                    <label className="check-row mt-8">
                      <input
                        type="checkbox"
                        checked={restrictTarget}
                        onChange={(e) => setRestrictTarget(e.target.checked)}
                      />
                      Ограничить доступ пользователя — объекта жалобы
                    </label>
                  )}
                  {(decisionStatus === "resolved" ||
                    decisionStatus === "dismissed" ||
                    restrictTarget) && (
                    <label className="check-row">
                      <input
                        type="checkbox"
                        checked={confirm}
                        onChange={(e) => setConfirm(e.target.checked)}
                      />
                      Подтверждаю решение: оно сохранится в журнале и будет отправлено заявителю
                    </label>
                  )}
                  <div className="actions">
                    <button
                      type="button"
                      className="primary"
                      disabled={
                        busy ||
                        !detail.allowed_statuses.includes(decisionStatus) ||
                        (isFinalDecision && comment.trim().length < 8) ||
                        (needsConfirm && !confirm)
                      }
                      onClick={() => void decide()}
                    >
                      Сохранить решение
                    </button>
                  </div>
                </div>
              ) : (
                <p className="info-box">
                  Ваша роль позволяет просматривать жалобы, но не принимать решения.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
