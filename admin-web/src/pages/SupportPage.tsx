import { useCallback, useEffect, useRef, useState } from "react";
import { api, statusBadgeClass, statusLabel } from "../api/client";
import type { AuthState } from "../api/client";

type Ticket = {
  id: string;
  number: string;
  subject: string;
  status: string;
  category: string | null;
  category_label: string | null;
  priority: string;
  created_at: string | null;
  updated_at: string | null;
  resolved_at: string | null;
};

type Message = {
  id: string;
  kind: string;
  author: string | null;
  body: string;
  internal: boolean;
  attachment: {
    kind: string;
    name: string | null;
    size: number | null;
    available: boolean;
    url: string | null;
  } | null;
  delivery: string;
  created_at: string | null;
};

type TicketDetail = Ticket & {
  messages: Message[];
  reopenable: boolean;
  resolved_by: string | null;
  confirmed_at: string | null;
  confirmed_by: string | null;
  disputed_at: string | null;
  confirmation_round: number;
  allowed_statuses: string[];
};

const STATUS_FILTERS: { key: string; label: string }[] = [
  { key: "", label: "Все" },
  { key: "new", label: "Новые" },
  { key: "in_progress", label: "В работе" },
  { key: "awaiting_confirmation", label: "Ждут подтверждения" },
  { key: "waiting_user", label: "Ждут уточнения" },
  { key: "needs_help", label: "Требуется помощь" },
  { key: "resolved", label: "Решённые" },
  { key: "closed", label: "Закрытые" },
];

const PRIORITY_LABEL: Record<string, string> = {
  low: "Низкий",
  normal: "Обычный",
  high: "Высокий",
  urgent: "Срочный",
};

const KIND_LABEL: Record<string, string> = {
  user: "Пользователь",
  staff: "Сотрудник",
  note: "Внутренняя заметка",
  system: "Система",
};

function formatBytes(size: number | null): string {
  if (!size) return "";
  if (size < 1024) return `${size} Б`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} КБ`;
  return `${(size / (1024 * 1024)).toFixed(1)} МБ`;
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString("ru-RU") : "—";
}

export function SupportPage({
  me,
  focusId,
  onChanged,
}: {
  me: AuthState;
  focusId?: string;
  onChanged?: () => void;
}) {
  const [items, setItems] = useState<Ticket[]>([]);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [reply, setReply] = useState("");
  const [internal, setInternal] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<{ url: string; label: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [showStatusPicker, setShowStatusPicker] = useState(false);
  const sending = useRef(false);
  const detailRef = useRef<HTMLDivElement | null>(null);

  const loadList = useCallback(async () => {
    const query = filter ? `?status=${encodeURIComponent(filter)}` : "";
    const data = await api<{ items: Ticket[] }>(`/support/tickets${query}`, me.csrf);
    setItems(data.items);
  }, [filter, me.csrf]);

  const openTicket = useCallback(
    async (id: string) => {
      setSelected(id);
      setError("");
      setNotice("");
      setShowStatusPicker(false);
      // A draft or note written for another ticket must not follow the selection.
      setReply("");
      setInternal(false);
      setPreview(null);
      try {
        setDetail(await api<TicketDetail>(`/support/tickets/${id}`, me.csrf));
        window.setTimeout(() => {
          const node = detailRef.current;
          if (!node) return;
          if (node.getBoundingClientRect().top > window.innerHeight - 120) {
            node.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        }, 0);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Обращение недоступно.");
        setDetail(null);
      }
    },
    [me.csrf],
  );

  useEffect(() => {
    void loadList().catch((e) => setError(e instanceof Error ? e.message : "Ошибка загрузки"));
  }, [loadList]);

  useEffect(() => {
    if (focusId) void openTicket(focusId);
  }, [focusId, openTicket]);

  async function refreshAfterAction() {
    if (selected) {
      setDetail(await api<TicketDetail>(`/support/tickets/${selected}`, me.csrf));
    }
    await loadList();
    onChanged?.();
  }

  async function sendReply(outcome: "answer" | "clarify" | "note") {
    if (!selected || !reply.trim() || busy || sending.current) return;
    sending.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    let res: { status: string; delivery: string; reserved?: boolean };
    try {
      res = await api<{ status: string; delivery: string }>(
        `/support/tickets/${selected}/reply`,
        me.csrf,
        {
          method: "POST",
          body: JSON.stringify({
            text: reply.trim(),
            internal: outcome === "note",
            outcome: outcome === "note" ? undefined : outcome,
          }),
        },
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ответ не отправлен.");
      sending.current = false;
      setBusy(false);
      return;
    }
    // The answer is stored. A failed refresh must not claim the save failed.
    setReply("");
    if (outcome === "note") {
      setNotice("Заметка сохранена. Пользователь её не увидит.");
    } else if (outcome === "answer") {
      setNotice(
        res.delivery === "delivered"
          ? "Ответ доставлен. Ждём, пока пользователь подтвердит решение."
          : "Ответ сохранён. Ждём подтверждения пользователя; доставка в Telegram ещё выполняется.",
      );
    } else {
      setNotice("Уточнение отправлено. Ждём ответа пользователя.");
    }
    try {
      await refreshAfterAction();
    } catch {
      setError("Ответ сохранён, но список не обновился. Обновите страницу.");
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  async function setStatus(status: string) {
    if (!selected || busy || sending.current) return;
    if (
      (status === "closed" || status === "resolved") &&
      !window.confirm(
        status === "closed"
          ? "Закрыть обращение? Пользователь больше не сможет ответить в этом обращении."
          : "Завершить обращение без нового ответа?",
      )
    ) {
      return;
    }
    sending.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/support/tickets/${selected}/status`, me.csrf, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      await refreshAfterAction();
      setNotice(`Статус изменён: ${statusLabel(status)}.`);
      setShowStatusPicker(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Статус не изменён.");
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <section className="stack">
      <div className="page-head">
        <div>
          <h1>Поддержка</h1>
          <p className="page-lead">
            Обращения из Telegram. «Отправить ответ» доставляет ответ и ждёт подтверждения
            пользователя в Telegram; «Запросить уточнение» — отдельный запрос информации.
            Внутренние заметки пользователю не отправляются.
          </p>
        </div>
      </div>
      {error && <p className="error-text">{error}</p>}
      {notice && <p className="ok-text">{notice}</p>}
      <div className="row">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className={filter === f.key ? "primary small" : "ghost small"}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>
      <div className="split">
        <div className="split-left panel ticket-list">
          {items.length === 0 ? (
            <p className="muted">По этому фильтру обращений нет.</p>
          ) : (
            items.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`ticket-row ${selected === t.id ? "active" : ""}`}
                onClick={() => void openTicket(t.id)}
              >
                <strong>{t.number}</strong>
                <span>{t.subject}</span>
                <span className={statusBadgeClass(t.status)}>{statusLabel(t.status)}</span>
                <span className="muted small">
                  {t.created_at ? `создано ${formatDate(t.created_at)}` : ""}
                </span>
              </button>
            ))
          )}
        </div>
        <div className="split-right panel" ref={detailRef}>
          {!detail ? (
            <p className="muted">Выберите обращение.</p>
          ) : (
            <>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h2>{detail.number}</h2>
                <span className={statusBadgeClass(detail.status)}>{statusLabel(detail.status)}</span>
              </div>
              <p>{detail.subject}</p>
              <p className="muted small">
                {detail.category_label || "Без категории"} · приоритет{" "}
                {PRIORITY_LABEL[detail.priority] || "Обычный"} · создано {formatDate(detail.created_at)}
              </p>
              <p className="muted small">
                Последнее изменение: {formatDate(detail.updated_at)}
                {detail.resolved_at
                  ? ` · завершено ${formatDate(detail.resolved_at)}${
                      detail.resolved_by ? ` (${detail.resolved_by})` : ""
                    }`
                  : ""}
              </p>
              {detail.confirmed_at && (
                <p className="muted small">
                  Подтверждено пользователем {detail.confirmed_by || ""} ·{" "}
                  {formatDate(detail.confirmed_at)}
                </p>
              )}
              {detail.disputed_at && !detail.confirmed_at && (
                <p className="muted small">
                  Пользователь сообщил о нерешённой проблеме · {formatDate(detail.disputed_at)}
                </p>
              )}
              {detail.status === "awaiting_confirmation" && (
                <p className="muted small">
                  Ожидаем подтверждения решения пользователем в Telegram.
                </p>
              )}
              <div className="status-actions mt-12">
                <button
                  type="button"
                  className="ghost small"
                  disabled={busy}
                  onClick={() => setShowStatusPicker((v) => !v)}
                >
                  {showStatusPicker ? "Скрыть смену статуса" : "Изменить статус вручную"}
                </button>
                {showStatusPicker &&
                  detail.allowed_statuses.map((status) => (
                    <button
                      key={status}
                      type="button"
                      className="ghost small"
                      disabled={busy}
                      onClick={() => void setStatus(status)}
                    >
                      {statusLabel(status)}
                    </button>
                  ))}
              </div>
              <div className="thread">
                {detail.messages.map((m) => (
                  <div key={m.id} className={`bubble ${m.internal ? "internal" : m.kind}`}>
                    <div className="bubble-meta">
                      <span className="muted">
                        {KIND_LABEL[m.kind] || "Сообщение"}
                        {m.kind === "staff" && m.author ? ` · ${m.author}` : ""}
                      </span>
                      {m.created_at && (
                        <span className="muted">{new Date(m.created_at).toLocaleString("ru-RU")}</span>
                      )}
                      {m.kind === "staff" && (
                        <span className={statusBadgeClass(m.delivery)}>
                          {statusLabel(m.delivery)}
                        </span>
                      )}
                    </div>
                    <div>{m.body}</div>
                    {m.attachment && (
                      <div className="attachment">
                        {!m.attachment.available || !m.attachment.url ? (
                          <span className="muted">
                            Вложение недоступно: файл не сохранён на сервере
                          </span>
                        ) : m.attachment.kind === "photo" ? (
                          <button
                            type="button"
                            className="support-attachment-thumb"
                            onClick={() =>
                              setPreview({
                                url: m.attachment!.url!,
                                label: `Фото из обращения ${detail.number}`,
                              })
                            }
                            aria-label="Открыть фото вложения"
                          >
                            <img
                              className="support-attachment-img"
                              src={m.attachment.url}
                              alt="Вложение из обращения"
                            />
                          </button>
                        ) : (
                          <div className="support-file-row">
                            <span>
                              Файл{m.attachment.name ? `: ${m.attachment.name}` : ""}
                              {m.attachment.size ? ` · ${formatBytes(m.attachment.size)}` : ""}
                            </span>
                            <a
                              className="ghost small"
                              href={m.attachment.url}
                              download={m.attachment.name || undefined}
                            >
                              Скачать
                            </a>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
              {detail.status === "closed" ? (
                <p className="muted">
                  Обращение закрыто. Чтобы ответить, сначала переоткройте его через «Изменить
                  статус вручную».
                </p>
              ) : (
                <>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={internal}
                      onChange={(e) => setInternal(e.target.checked)}
                    />
                    Внутренняя заметка (пользователь не увидит)
                  </label>
                  <textarea
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                    placeholder={
                      internal ? "Заметка для команды" : "Ответ пользователю"
                    }
                  />
                  {internal ? (
                    <button
                      type="button"
                      className="primary mt-8"
                      disabled={busy || !reply.trim()}
                      onClick={() => void sendReply("note")}
                    >
                      Сохранить заметку
                    </button>
                  ) : (
                    <div className="row mt-8">
                      <button
                        type="button"
                        className="primary"
                        disabled={busy || !reply.trim()}
                        onClick={() => void sendReply("answer")}
                      >
                        Отправить ответ
                      </button>
                      <button
                        type="button"
                        className="ghost"
                        disabled={busy || !reply.trim()}
                        onClick={() => void sendReply("clarify")}
                      >
                        Запросить уточнение
                      </button>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
      {preview && (
        <div
          className="lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={preview.label}
          onClick={() => setPreview(null)}
        >
          <div className="lightbox-inner" onClick={(e) => e.stopPropagation()}>
            <img className="lightbox-img" src={preview.url} alt={preview.label} />
            <div className="lightbox-bar">
              <span className="muted">{preview.label}</span>
              <div className="lightbox-actions">
                <button
                  type="button"
                  className="ghost small"
                  onClick={() => setPreview(null)}
                  aria-label="Закрыть"
                >
                  ✕
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
