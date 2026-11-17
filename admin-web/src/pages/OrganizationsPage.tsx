import { useCallback, useEffect, useRef, useState } from "react";
import { api, statusBadgeClass, statusLabel } from "../api/client";
import type { AuthState } from "../api/client";
import { DataTable } from "../components/DataTable";

type OrgRow = {
  id: string;
  name: string;
  participant_type: string;
  country: string | null;
  website_url: string | null;
  review_status: string;
  pending_representatives: number;
};

type Membership = {
  id: string;
  role: string | null;
  status: string;
  claims_direct_investment_mandate: boolean;
  verified_at: string | null;
  decision_reason: string | null;
  user: { id: string | null; telegram_id: number | null; username: string | null; name: string | null };
};

type OrgDetail = OrgRow & {
  description: string | null;
  participant_type_label?: string | null;
  support_modes: string[];
  support_mode_labels?: string[];
  open_for_requests: boolean;
  created_at: string | null;
  memberships: Membership[];
  can_verify_memberships: boolean;
};

export function OrganizationsPage({
  me,
  canReview,
  focusId,
  onChanged,
}: {
  me: AuthState;
  canReview: boolean;
  focusId?: string;
  onChanged?: () => void;
}) {
  const [items, setItems] = useState<OrgRow[]>([]);
  const [detail, setDetail] = useState<OrgDetail | null>(null);
  const [reason, setReason] = useState("");
  const [officialName, setOfficialName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const data = await api<{ items: OrgRow[] }>("/organizations", me.csrf);
    setItems(data.items);
  }, [me.csrf]);

  const open = useCallback(
    async (id: string) => {
      setError("");
      setReason("");
      try {
        const data = await api<OrgDetail>(`/organizations/${id}`, me.csrf);
        setDetail(data);
        setOfficialName(data.name);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Организация недоступна.");
        setDetail(null);
      }
    },
    [me.csrf],
  );

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : "Ошибка загрузки"));
  }, [load]);

  useEffect(() => {
    if (focusId) void open(focusId);
  }, [focusId, open]);

  async function reviewOrg(decision: "approve" | "reject" | "changes") {
    if (!detail) return;
    if (decision !== "approve" && reason.trim().length < 8) {
      setError("Причина должна быть не короче 8 символов.");
      return;
    }
    if (
      decision === "reject" &&
      !window.confirm("Отклонить организацию? Решение уйдёт заявителю и сохранится в журнале.")
    ) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(`/organizations/${detail.id}/review`, me.csrf, {
        method: "POST",
        body: JSON.stringify({
          decision,
          reason: decision === "approve" ? undefined : reason.trim(),
          official_name: decision === "approve" ? officialName.trim() || undefined : undefined,
        }),
      });
      setReason("");
      await open(detail.id);
      await load();
      onChanged?.();
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Решение не применено.");
    } finally {
      setBusy(false);
    }
  }

  async function reviewMembership(membership: Membership, decision: "approve" | "reject" | "revoke", mandate?: boolean) {
    if (decision !== "approve" && reason.trim().length < 8) {
      setError("Причина должна быть не короче 8 символов.");
      return;
    }
    if (
      decision === "reject" &&
      !window.confirm("Отклонить организацию? Решение уйдёт заявителю и сохранится в журнале.")
    ) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(`/organizations/memberships/${membership.id}/review`, me.csrf, {
        method: "POST",
        body: JSON.stringify({
          decision,
          reason: decision === "approve" ? undefined : reason.trim(),
          mandate,
        }),
      });
      setReason("");
      if (detail) await open(detail.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Решение не применено.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="stack">
      <div className="page-head">
        <div>
          <h1>Организации</h1>
          <p className="page-lead">
            Подтверждение фонда и подтверждение представителя — два отдельных решения. Подтверждённая
            организация не означает подтверждённого сотрудника или гарантированный капитал.
          </p>
        </div>
        {!canReview && <span className="badge info">Только просмотр</span>}
      </div>
      {error && <p className="error-text">{error}</p>}
      <div className="split">
        <div className="split-left">
          <DataTable
            columns={[
              { key: "name", label: "Организация" },
              { key: "participant_type", label: "Тип" },
              { key: "country", label: "Страна" },
              {
                key: "review_status",
                label: "Статус",
                render: (row) => (
                  <span className={statusBadgeClass(String(row.review_status))}>
                    {statusLabel(String(row.review_status))}
                  </span>
                ),
              },
              { key: "pending_representatives", label: "Представители" },
            ]}
            rows={items as unknown as Record<string, unknown>[]}
            onRowClick={(row) => void open(String(row.id))}
            emptyText="Организаций пока нет."
          />
        </div>
        <div className="split-right panel stack">
          {!detail ? (
            <p className="muted">Выберите организацию.</p>
          ) : (
            <>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h2>{detail.name}</h2>
                <span className={statusBadgeClass(detail.review_status)}>
                  {statusLabel(detail.review_status)}
                </span>
              </div>
              <p className="muted small">
                {detail.participant_type_label || detail.participant_type}
                {detail.country ? ` · ${detail.country}` : ""}
                {detail.website_url ? ` · ${detail.website_url}` : ""}
              </p>
              {detail.description && <p>{detail.description}</p>}
              <div className="field-grid">
                <div>
                  <div className="muted field-key">Режимы поддержки</div>
                  <div>{detail.support_mode_labels?.length ? detail.support_mode_labels.join(", ") : "—"}</div>
                </div>
                <div>
                  <div className="muted field-key">Открыта для запросов</div>
                  <div>{detail.open_for_requests ? "да" : "нет"}</div>
                </div>
              </div>
              {canReview && (
                <div className="stack">
                  <input
                    placeholder="Официальное название (при одобрении)"
                    value={officialName}
                    onChange={(e) => setOfficialName(e.target.value)}
                  />
                  <textarea
                    placeholder="Причина отказа/доработки (уйдёт заявителю)"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                  <div className="actions">
                    <button
                      type="button"
                      className="primary"
                      disabled={busy || detail.review_status === "approved"}
                      onClick={() => void reviewOrg("approve")}
                    >
                      Подтвердить организацию
                    </button>
                    <button
                      type="button"
                      className="ghost"
                      disabled={busy}
                      onClick={() => void reviewOrg("changes")}
                    >
                      Запросить уточнения
                    </button>
                    <button
                      type="button"
                      className="danger"
                      disabled={busy}
                      onClick={() => void reviewOrg("reject")}
                    >
                      Отклонить
                    </button>
                  </div>
                </div>
              )}
              <h3 className="mt-12">Представители</h3>
              {detail.memberships.length === 0 ? (
                <p className="muted">Представителей нет.</p>
              ) : (
                <div className="stack">
                  {detail.memberships.map((m) => (
                    <div key={m.id} className="panel stack">
                      <div className="row" style={{ justifyContent: "space-between" }}>
                        <strong>
                          {m.user.name || m.user.username || m.user.telegram_id || "Пользователь"}
                        </strong>
                        <span className={statusBadgeClass(m.status)}>{statusLabel(m.status)}</span>
                      </div>
                      <div className="muted small">
                        {m.role || "роль не указана"} ·{" "}
                        {m.claims_direct_investment_mandate
                          ? "заявлен мандат прямых инвестиций"
                          : "без мандата прямых инвестиций"}
                      </div>
                      {m.decision_reason && <div className="muted small">Причина: {m.decision_reason}</div>}
                      {canReview && m.status !== "revoked" && (
                        <div className="actions">
                          <button
                            type="button"
                            className="primary small"
                            disabled={busy || !detail.can_verify_memberships || m.status === "approved"}
                            onClick={() => void reviewMembership(m, "approve", true)}
                          >
                            Подтвердить с мандатом
                          </button>
                          <button
                            type="button"
                            className="ghost small"
                            disabled={busy || !detail.can_verify_memberships || m.status === "approved"}
                            onClick={() => void reviewMembership(m, "approve", false)}
                          >
                            Без мандата
                          </button>
                          {["approved", "under_review"].includes(m.status) && (
                            <button
                              type="button"
                              className="danger small"
                              disabled={busy}
                              onClick={() => void reviewMembership(m, "revoke")}
                            >
                              Отозвать
                            </button>
                          )}
                        </div>
                      )}
                      {!detail.can_verify_memberships && m.status !== "revoked" && (
                        <p className="muted small">
                          Сначала подтвердите организацию, затем представителя.
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
