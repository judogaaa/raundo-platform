import { useCallback, useEffect, useState } from "react";
import { api, statusBadgeClass, statusLabel } from "../api/client";
import type { AuthState } from "../api/client";
import { DataTable } from "../components/DataTable";
import { ExpandableText } from "../components/ExpandableText";
import { InvestorReviewDetails } from "../components/InvestorReviewDetails";
import { ReviewDiff } from "../components/ReviewDiff";
import { ReviewMedia, mediaScanLabel } from "../components/ReviewMedia";

type QueueItem = {
  review_id: string;
  kind: string;
  title: string;
  state: string;
  revision: number;
  submitted_at: string;
};

type InvestorProfileView = {
  investor_type_label: string;
  strategy: string;
  cheque: string;
  sectors: string[];
  stages: string[];
  geography: string;
  support_modes: string[];
  position: string;
  public_reference_url: string;
  open_for_requests: boolean;
};

type InvestorOrganization = {
  name: string;
  type: string;
  position: string;
  website_url: string;
  review_status: string;
  membership_status: string;
  affiliation_status: string;
  mandate_verified: boolean;
  mandate_label: string;
};

type Detail = {
  kind: string;
  review_id: string;
  title: string;
  state: string;
  revision: number;
  current_revision: number;
  submitted_at?: string;
  has_published?: boolean;
  fields: Record<string, unknown>;
  profile?: InvestorProfileView;
  organization?: InvestorOrganization | null;
  diff: { field: string; published: unknown; pending: unknown }[];
  diff_view?: { label: string; before: string; after: string }[];
  media?: { position: number; kind: string; scan_state: string; preview_url?: string }[];
  history: { revision: number; state: string; comment?: string; submitted_at: string }[];
  owner: { username?: string; name?: string; telegram_id?: number };
  organization_claim?: { name: string; status: string };
};

const KIND_LABEL: Record<string, string> = { startup: "Стартап", investor: "Инвестор" };

const NOT_SET = "Не указано";

function fmtDate(iso?: string): string {
  if (!iso) return NOT_SET;
  try {
    return new Date(iso).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function field(key: string, fields: Record<string, unknown>): string {
  const v = fields[key];
  return v === null || v === undefined ? "" : String(v).trim();
}

/** Startup-specific repainting of diff values (state codes → labels). */
function startupDiffDescribe(fieldKey: string, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (fieldKey === "state") return statusLabel(String(value)) || NOT_SET;
  return String(value).trim();
}

/** Investor diff: same contract (raw state → label, empty → "Не указано"). */
function investorDiffDescribe(fieldKey: string, value: unknown): string {
  return startupDiffDescribe(fieldKey, value);
}

function OwnerLine({ value }: { value?: string | number }) {
  return <span>{value ? String(value) : NOT_SET}</span>;
}

export function ModerationPage({
  items,
  me,
  onReload,
  focusId,
}: {
  items: QueueItem[];
  me: AuthState;
  onReload: () => void;
  focusId?: string;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const loadDetail = useCallback(
    async (reviewId: string, kind: string) => {
      setSelected(reviewId);
      setError("");
      // A comment written for another applicant must never satisfy this form.
      setComment("");
      const path =
        kind === "investor"
          ? `/moderation/investors/reviews/${reviewId}`
          : `/moderation/startups/reviews/${reviewId}`;
      try {
        setDetail(await api<Detail>(path, me.csrf));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Не удалось загрузить заявку.");
        setDetail(null);
      }
    },
    [me.csrf],
  );

  useEffect(() => {
    if (!focusId) return;
    const hit = items.find((i) => i.review_id === focusId);
    if (hit) {
      void loadDetail(hit.review_id, hit.kind);
      return;
    }
    // The review may already be decided: try both review kinds once.
    void (async () => {
      try {
        setSelected(focusId);
        setDetail(await api<Detail>(`/moderation/startups/reviews/${focusId}`, me.csrf));
      } catch {
        try {
          setDetail(await api<Detail>(`/moderation/investors/reviews/${focusId}`, me.csrf));
        } catch {
          setError("Заявка уже обработана или недоступна.");
        }
      }
    })();
  }, [focusId, items, loadDetail, me.csrf]);

  async function act(action: "approve" | "changes" | "reject") {
    if (!detail) return;
    if ((action === "changes" || action === "reject") && comment.trim().length < 8) {
      setError("Комментарий должен быть не короче 8 символов — он уходит основателю.");
      return;
    }
    if (
      action === "reject" &&
      !window.confirm("Отклонить анкету? Решение уйдёт автору и сохранится в журнале.")
    ) {
      return;
    }
    if (
      action === "approve" &&
      !window.confirm("Одобрить и опубликовать? Анкета сразу появится в ленте RAUNDO.")
    ) {
      return;
    }
    setBusy(true);
    setError("");
    const base =
      detail.kind === "investor"
        ? `/moderation/investors/${detail.review_id}`
        : `/moderation/startups/${detail.review_id}`;
    const path =
      action === "approve" ? `${base}/approve` : action === "changes" ? `${base}/changes` : `${base}/reject`;
    try {
      await api(path, me.csrf, {
        method: "POST",
        body: action === "approve" ? undefined : JSON.stringify({ comment: comment.trim() }),
      });
      setComment("");
      setDetail(null);
      setSelected(null);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Решение не применено.");
      // Stale revision / already decided: the queue is outdated, refresh it.
      setDetail(null);
      setSelected(null);
      onReload();
    } finally {
      setBusy(false);
    }
  }

  function nextInQueue() {
    const idx = items.findIndex((i) => i.review_id === selected);
    const next = items[idx + 1] ?? items[0];
    if (next) void loadDetail(next.review_id, next.kind);
  }

  const stale = detail !== null && detail.revision !== detail.current_revision;
  const f = detail?.fields ?? {};

  return (
    <section className="stack">
      <div className="page-head">
        <div>
          <h1>Модерация</h1>
          <p className="page-lead">
            Ручная проверка стартапов и инвесторов. Одобрение, запрос изменений и отказ уходят
            основателю через очередь уведомлений и пишутся в журнал.
          </p>
        </div>
        <span className="badge info">В очереди: {items.length}</span>
      </div>
      {error && <p className="error-text">{error}</p>}
      <div className="split">
        <div className="split-left">
          <DataTable
            columns={[
              { key: "title", label: "Название" },
              {
                key: "kind",
                label: "Тип",
                render: (row) => KIND_LABEL[String(row.kind)] || String(row.kind),
              },
              {
                key: "state",
                label: "Статус",
                render: (row) => (
                  <span className={statusBadgeClass(String(row.state))}>
                    {statusLabel(String(row.state))}
                  </span>
                ),
              },
              { key: "revision", label: "Редакция" },
            ]}
            rows={items as unknown as Record<string, unknown>[]}
            rowKey={(row) => String(row.review_id)}
            onRowClick={(row) => void loadDetail(String(row.review_id), String(row.kind))}
            emptyText="Очередь пуста — все заявки рассмотрены."
          />
        </div>
        <div className="split-right panel review-card">
          {!detail ? (
            <p className="muted">Выберите заявку в списке слева.</p>
          ) : (
            <>
              {/* ── Шапка ─────────────────────────────────────────── */}
              <div className="review-head">
                <div className="review-head-row">
                  {detail.kind === "investor" && detail.media?.[0]?.preview_url && (
                    <img
                      className="review-avatar"
                      src={detail.media[0].preview_url}
                      alt={detail.title}
                      loading="lazy"
                    />
                  )}
                  <div className="review-head-main">
                    <div className="review-badges">
                      <span className="badge info">{KIND_LABEL[detail.kind] || detail.kind}</span>
                      <span className={statusBadgeClass(detail.state)}>{statusLabel(detail.state)}</span>
                      <span className="badge">Редакция {detail.revision}</span>
                    </div>
                    <h2 className="review-title">{detail.title}</h2>
                    <p className="muted small review-owner-line">
                      Владелец:{" "}
                      <OwnerLine
                        value={
                          detail.owner.username
                            ? `@${detail.owner.username}`
                            : detail.owner.name || detail.owner.telegram_id
                        }
                      />
                      {" · "}
                      отправлено {fmtDate(detail.submitted_at)}
                    </p>
                  </div>
                </div>
              </div>
              {stale && (
                <div className="callout warn">
                  Заявка устарела: основатель уже отправил редакцию {detail.current_revision}.
                  Обновите очередь.
                </div>
              )}

              {detail.kind === "startup" ? (
                <>
                  {/* ── Контент: 2 колонки ─────────────────────────── */}
                  <div className="review-columns">
                    <div className="review-col">
                      <div className="review-block">
                        <h3>О проекте</h3>
                        <div className="review-value">
                          <div className="muted field-key">Описание</div>
                          <ExpandableText value={field("Описание", f)} />
                        </div>
                        <div className="review-value">
                          <div className="muted field-key">Питч</div>
                          <ExpandableText value={field("Питч", f)} />
                        </div>
                      </div>
                    </div>
                    <div className="review-col">
                      <div className="review-block">
                        <h3>Параметры</h3>
                        <div className="review-params">
                          {[
                            ["География", field("География", f)],
                            ["Стадия", field("Стадия", f)],
                            ["Монетизация", field("Монетизация", f)],
                            ["Запрос инвестиций", field("Запрос", f)],
                            ["Статус анкеты", field("Статус анкеты", f)],
                          ].map(([key, value]) => (
                            <div key={key} className="review-param">
                              <div className="muted field-key">{key}</div>
                              <div>{value || NOT_SET}</div>
                            </div>
                          ))}
                        </div>
                        <div className="review-value">
                          <div className="muted field-key">Медиа</div>
                          <ReviewMedia media={detail.media} />
                        </div>
                      </div>
                    </div>
                  </div>

                  {detail.organization_claim && (
                    <div className="info-box">
                      Заявленная организация: {detail.organization_claim.name} (
                      {statusLabel(detail.organization_claim.status)})
                    </div>
                  )}

                  {/* ── Что изменилось ─────────────────────────────── */}
                  <div className="review-block">
                    <h3>Что изменилось</h3>
                    <ReviewDiff diff={detail.diff} diffView={detail.diff_view} describe={startupDiffDescribe} />
                  </div>
                </>
              ) : detail.profile ? (
                <>
                  <InvestorReviewDetails
                    profile={detail.profile}
                    organization={detail.organization}
                    media={detail.media}
                  />
                  {detail.has_published ? (
                    <div className="review-block">
                      <h3>Что изменилось</h3>
                      <ReviewDiff diff={detail.diff} diffView={detail.diff_view} describe={investorDiffDescribe} />
                    </div>
                  ) : (
                    <div className="review-block">
                      <h3>Что изменилось</h3>
                      <p className="review-text muted">
                        Это первая заявка инвестора — сравнивать не с чем. Ниже история решений.
                      </p>
                    </div>
                  )}
                </>
              ) : (
                <p className="muted">Профиль инвестора недоступен.</p>
              )}

              {/* ── Статус медиа / история ─────────────────────────── */}
              {detail.kind === "startup" && detail.media && detail.media.length > 0 && (
                <p className="muted small">
                  {detail.media.map((m) => `Фото ${m.position}: ${mediaScanLabel(m.scan_state)}`).join(" · ")}
                </p>
              )}

              <div className="review-block">
                <h3>История решений</h3>
                {detail.history.length === 0 ? (
                  <p className="muted review-text">Пока нет решений по этой заявке.</p>
                ) : (
                  <ul className="history-list">
                    {detail.history.map((h) => (
                      <li key={`${h.revision}-${h.submitted_at}`}>
                        Редакция {h.revision} · {statusLabel(h.state)} · {fmtDate(h.submitted_at)}
                        {h.comment ? ` — ${h.comment}` : ""}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* ── Действия ───────────────────────────────────────── */}
              <div className="review-block review-actions-block">
                <h3>Действия</h3>
                <textarea
                  placeholder="Комментарий модератора — нужен для «Запросить изменения» или «Отклонить», уйдёт основателю"
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                />
                <div className="actions">
                  <button
                    type="button"
                    className="primary"
                    disabled={busy || stale}
                    onClick={() => void act("approve")}
                  >
                    Одобрить
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    disabled={busy || stale}
                    onClick={() => void act("changes")}
                  >
                    Запросить изменения
                  </button>
                  <button
                    type="button"
                    className="danger"
                    disabled={busy || stale}
                    onClick={() => void act("reject")}
                  >
                    Отклонить
                  </button>
                  <button type="button" className="ghost" onClick={nextInQueue}>
                    Следующая
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
