import { useEffect, useState, type ReactNode } from "react";
import { api, statusBadgeClass, statusLabel } from "../api/client";
import { DataTable } from "../components/DataTable";

export type SimpleColumn = {
  key: string;
  label: string;
  render?: (row: Record<string, unknown>) => ReactNode;
};

export function SimpleTablePage({
  title,
  lead,
  columns,
  path,
  csrf,
  emptyText,
}: {
  title: string;
  lead?: string;
  columns: SimpleColumn[];
  path: string;
  csrf: string;
  emptyText?: string;
}) {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<{ items: Record<string, unknown>[] }>(path, csrf)
      .then((data) => {
        if (!cancelled) setRows(data.items);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Ошибка загрузки");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [path, csrf]);

  return (
    <section className="stack">
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          {lead && <p className="page-lead">{lead}</p>}
        </div>
      </div>
      {error && <p className="error-text">{error}</p>}
      {loading ? (
        <p className="muted">Загрузка…</p>
      ) : (
        <DataTable columns={columns} rows={rows} emptyText={emptyText} />
      )}
    </section>
  );
}

const dateCell = (row: Record<string, unknown>) =>
  row.created_at ? new Date(String(row.created_at)).toLocaleString("ru-RU") : "—";

const PLAN_LABEL: Record<string, string> = {
  community_plus: "Community PLUS",
  startup_pro: "Startup PRO",
  investor_pro: "Investor PRO",
  organization_pro: "Organization PRO",
};

const OWNER_LABEL: Record<string, string> = {
  user: "Пользователь",
  startup: "Стартап",
  organization: "Организация",
};

const SOURCE_LABEL: Record<string, string> = {
  test_grant: "Тестовое право",
  stars: "Telegram Stars",
  admin: "Администратор",
};

const RESOURCE_LABEL: Record<string, string> = {
  startup: "Стартап",
  investor: "Инвестор",
  organization: "Организация",
  organization_membership: "Представитель",
  membership: "Представитель",
  admin_account: "Администратор",
  support_ticket: "Обращение",
  ticket: "Обращение",
  complaint: "Жалоба",
  contact_request: "Запрос на знакомство",
  user: "Пользователь",
};

const ACTION_LABEL: Record<string, string> = {
  approve: "Одобрение",
  reject: "Отклонение",
  request_changes: "Запрос изменений",
  changes: "Запрос изменений",
  changes_requested: "Запрос изменений",
  restrict: "Ограничение доступа",
  unrestrict: "Снятие ограничения",
  mfa_enable: "Включение MFA",
  mfa_disable: "Отключение MFA",
  staff_enable: "Включение сотрудника",
  staff_disable: "Отключение сотрудника",
  staff_role: "Смена роли",
  org_review: "Проверка организации",
  membership_review: "Проверка представителя",
  complaint_resolve: "Решение по жалобе",
  complaint_status: "Статус жалобы",
  ticket_reply: "Ответ поддержки",
  ticket_status: "Статус обращения",
};

function stateLabel(value: unknown): string {
  if (!value || typeof value !== "object") return "—";
  const record = value as Record<string, unknown>;
  const status = record.status ?? record.review_status ?? record.mfa_enabled;
  if (typeof status === "string") return statusLabel(status);
  if (typeof status === "boolean") return status ? "включено" : "выключено";
  return "—";
}

function changeCell(row: Record<string, unknown>): string {
  const before = stateLabel(row.before_state);
  const after = stateLabel(row.after_state);
  if (before === "—" && after === "—") return "—";
  return before === "—" ? after : `${before} → ${after}`;
}

type PaymentRow = {
  id: string;
  number: string;
  user: string;
  plan: string;
  period: string;
  amount: string;
  provider: string;
  status: string;
  created_at: string | null;
  paid_at: string | null;
  premium_until: string | null;
  is_test: boolean;
};

type PaymentDetail = {
  number: string;
  status: string;
  plan_code: string;
  duration: string;
  amount_minor: number;
  currency: string;
  provider: string;
  user: string | null;
  consent_version: string | null;
  consent_accepted_at: string | null;
  paid_at: string | null;
  is_test: boolean;
  transactions: {
    provider: string;
    provider_payment_id: string;
    provider_charge_id: string | null;
    provider_status: string;
    amount_minor: number;
    currency: string;
    refund_status: string | null;
    processed_at: string | null;
  }[];
};

const PROVIDER_LABELS: Record<string, string> = {
  yookassa: "Карта / СБП",
  stars: "Telegram Stars",
  demo: "Тестовый режим",
};

function PaymentsTable({ csrf }: { csrf: string }) {
  const [items, setItems] = useState<PaymentRow[]>([]);
  const [detail, setDetail] = useState<PaymentDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api<{ items: PaymentRow[] }>("/payments", csrf)
      .then((data) => setItems(data.items))
      .catch((e) => setError(e instanceof Error ? e.message : "Не удалось загрузить платежи."))
      .finally(() => setLoading(false));
  }, [csrf]);

  async function open(id: string) {
    try {
      setDetail(await api<PaymentDetail>(`/payments/${id}`, csrf));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Платёж недоступен.");
    }
  }

  return (
    <div className="panel stack">
      <div className="panel-head">
        <h2>Платежи</h2>
      </div>
      {error && <p className="error-text">{error}</p>}
      {loading ? (
        <p className="muted">Загрузка платежей…</p>
      ) : (
        <DataTable
          columns={[
            { key: "number", label: "Заказ" },
            { key: "user", label: "Пользователь" },
            { key: "plan", label: "Тариф" },
            { key: "amount", label: "Сумма" },
            {
              key: "provider",
              label: "Способ",
              render: (row) => PROVIDER_LABELS[String(row.provider)] || "—",
            },
            {
              key: "status",
              label: "Статус",
              render: (row) => (
                <span className={statusBadgeClass(String(row.status))}>
                  {statusLabel(String(row.status))}
                </span>
              ),
            },
            {
              key: "paid_at",
              label: "Оплачен",
              render: (row) => (row.paid_at ? new Date(String(row.paid_at)).toLocaleString("ru-RU") : "—"),
            },
          ]}
          rows={items as unknown as Record<string, unknown>[]}
          onRowClick={(row) => void open(String(row.id))}
          emptyText="Платежей пока нет."
        />
      )}
      {detail && (
        <div className="stack">
          <h3>{detail.number}</h3>
          <p className="muted small">
            {PROVIDER_LABELS[detail.provider] || detail.provider} · {statusLabel(detail.status)}
            {detail.is_test ? " · тестовый" : ""}
          </p>
          <p className="small">
            Согласие: {detail.consent_version || "—"} ·{" "}
            {detail.consent_accepted_at
              ? new Date(detail.consent_accepted_at).toLocaleString("ru-RU")
              : "не зафиксировано"}
          </p>
          <ul className="timeline">
            {detail.transactions.map((tx) => (
              <li key={tx.provider_payment_id}>
                <span className="timeline-dot" aria-hidden="true" />
                <div>
                  <strong>{PROVIDER_LABELS[tx.provider] || tx.provider}</strong>
                  <div className="muted small">
                    {tx.provider_status} · {tx.amount_minor / 100} {tx.currency}
                    {tx.refund_status ? ` · возврат: ${tx.refund_status}` : ""}
                  </div>
                  <div className="mono small">{tx.provider_payment_id}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function PremiumPage({ csrf }: { csrf: string }) {
  return (
    <div className="stack">
    <SimpleTablePage
      title="Premium"
      lead="Реальные записи подписок и платежей. Сумма и тариф фиксируются на сервере; в админке они только для чтения."
      path="/premium"
      csrf={csrf}
      emptyText="Подписок нет."
      columns={[
        {
          key: "plan",
          label: "План",
          render: (row) => PLAN_LABEL[String(row.plan)] || String(row.plan ?? "—"),
        },
        {
          key: "owner_type",
          label: "Владелец",
          render: (row) => OWNER_LABEL[String(row.owner_type)] || "Пользователь",
        },
        {
          key: "status",
          label: "Статус",
          render: (row) => (
            <span className={statusBadgeClass(String(row.status))}>{statusLabel(String(row.status))}</span>
          ),
        },
        {
          key: "source",
          label: "Источник",
          render: (row) => SOURCE_LABEL[String(row.source)] || "Система",
        },
        {
          key: "is_test",
          label: "Тестовая",
          render: (row) => (row.is_test ? "да" : "нет"),
        },
        {
          key: "expires_at",
          label: "Истекает",
          render: (row) =>
            row.expires_at ? new Date(String(row.expires_at)).toLocaleString("ru-RU") : "—",
        },
      ]}
    />
    <PaymentsTable csrf={csrf} />
    <ReceiptsPanel csrf={csrf} />
    </div>
  );
}


type ReceiptRow = {
  id: string;
  order_number: string;
  amount_minor: number;
  currency: string;
  status: string;
  provider?: string | null;
  fiscal_receipt_id?: string | null;
  paid_at?: string | null;
  registered_at?: string | null;
  sent_to_user: boolean;
  error?: string | null;
};

const RECEIPT_STATUS: Record<string, string> = {
  pending: "Ожидает чека",
  registered: "Чек зарегистрирован",
  failed: "Ошибка",
  refunded: "Возврат",
};

function ReceiptsPanel({ csrf }: { csrf: string }) {
  const [items, setItems] = useState<ReceiptRow[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const data = await api<{ counts: Record<string, number>; items: ReceiptRow[] }>(
        "/tax-receipts",
        csrf,
      );
      setItems(data.items);
      setCounts(data.counts ?? {});
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить реестр чеков.");
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [csrf]);

  async function register(row: ReceiptRow) {
    const fiscal = window.prompt("Номер чека в «Мой налог» (обязательно):", "");
    if (!fiscal || !fiscal.trim()) return;
    const url = window.prompt("Ссылка на чек (необязательно):", "") || undefined;
    setBusy(true);
    try {
      await api(`/tax-receipts/${row.id}/registered`, csrf, {
        method: "POST",
        body: JSON.stringify({
          provider: "my_tax",
          fiscal_receipt_id: fiscal.trim(),
          receipt_url: url,
          sent_to_user: false,
        }),
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить чек.");
    } finally {
      setBusy(false);
    }
  }

  async function fail(row: ReceiptRow) {
    const reason = window.prompt("Причина ошибки:", "");
    if (!reason || !reason.trim()) return;
    setBusy(true);
    try {
      await api(`/tax-receipts/${row.id}/failed`, csrf, {
        method: "POST",
        body: JSON.stringify({ error: reason.trim() }),
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить ошибку.");
    } finally {
      setBusy(false);
    }
  }

  const queue = items.filter((row) => row.status === "pending" || row.status === "failed");

  return (
    <div className="panel stack">
      <div className="panel-head">
        <h2>Чеки НПД</h2>
      </div>
      <p className="muted small">
        Ожидают чека: {counts.pending ?? 0}. Ошибки: {counts.failed ?? 0}. Чек формируется в
        «Мой налог», здесь фиксируется реальный номер и ссылка. Приложение чек не подменяет.
      </p>
      {error && <p className="error-text">{error}</p>}
      {queue.length === 0 ? (
        <p className="muted">Нет оплаченных заказов без чека.</p>
      ) : (
        <div className="stack">
          {queue.map((row) => (
            <div key={row.id} className="panel stack" style={{ padding: "12px" }}>
              <div className="muted small">
                {row.order_number} · {(row.amount_minor / 100).toFixed(2)} {row.currency} ·{" "}
                {RECEIPT_STATUS[row.status] || row.status}
                {row.error ? ` · ${row.error}` : ""}
              </div>
              <div className="row-actions" style={{ display: "flex", gap: "8px" }}>
                <button type="button" disabled={busy} onClick={() => void register(row)}>
                  Отметить чек
                </button>
                <button type="button" disabled={busy} onClick={() => void fail(row)}>
                  Ошибка
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function AuditPage({ csrf }: { csrf: string }) {
  return (
    <SimpleTablePage
      title="Журнал действий"
      lead="Каждое решение фиксируется с автором, до и после состояния. Записи не редактируются из панели."
      path="/audit"
      csrf={csrf}
      emptyText="Действий пока нет."
      columns={[
        {
          key: "action",
          label: "Действие",
          render: (row) => ACTION_LABEL[String(row.action)] || String(row.action ?? "—"),
        },
        { key: "actor", label: "Автор" },
        {
          key: "resource_type",
          label: "Ресурс",
          render: (row) =>
            RESOURCE_LABEL[String(row.resource_type)] || String(row.resource_type ?? "—"),
        },
        {
          key: "change",
          label: "Изменение",
          render: (row) => <span className="small">{changeCell(row)}</span>,
        },
        {
          key: "reason",
          label: "Причина",
          render: (row) => (row.reason ? String(row.reason) : "—"),
        },
        { key: "created_at", label: "Время", render: dateCell },
      ]}
    />
  );
}
