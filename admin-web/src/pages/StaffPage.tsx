import { useCallback, useEffect, useState } from "react";
import { api, roleLabel, statusBadgeClass, statusLabel } from "../api/client";
import type { AuthState } from "../api/client";
import { DataTable } from "../components/DataTable";

type Staff = {
  id: string;
  login: string;
  role: string;
  disabled: boolean;
  mfa: boolean;
  must_change_password: boolean;
};

type Invite = { id: string; login: string; role: string; expires_at: string };

const ROLE_LABEL: Record<string, string> = {
  owner: "Владелец",
  administrator: "Администратор",
  moderator: "Модератор",
  support: "Поддержка",
};

export function StaffPage({ me }: { me: AuthState }) {
  const [items, setItems] = useState<Staff[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [login, setLogin] = useState("");
  const [role, setRole] = useState("moderator");
  const [inviteToken, setInviteToken] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const data = await api<{ items: Staff[]; invites: Invite[] }>("/staff", me.csrf);
    setItems(data.items);
    setInvites(data.invites);
  }, [me.csrf]);

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : "Ошибка загрузки"));
  }, [load]);

  async function run(fn: () => Promise<unknown>, success?: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
      if (success) setMsg(success);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Операция не выполнена.");
    } finally {
      setBusy(false);
    }
  }

  async function invite() {
    if (login.trim().length < 3) {
      setError("Логин должен быть не короче 3 символов.");
      return;
    }
    setInviteToken("");
    await run(async () => {
      const res = await api<{ invite_token: string }>("/staff/invites", me.csrf, {
        method: "POST",
        body: JSON.stringify({ login: login.trim(), role }),
      });
      setInviteToken(res.invite_token);
      setLogin("");
    }, "Приглашение создано.");
  }

  return (
    <section className="stack">
      <div className="page-head">
        <div>
          <h1>Сотрудники</h1>
          <p className="page-lead">
            Роли и доступ к панели. Владелец не может быть отключён или понижен. Приглашение
            действует 48 часов, токен показывается один раз.
          </p>
        </div>
      </div>
      {error && <p className="error-text">{error}</p>}
      {msg && <p className="ok-text">{msg}</p>}
      <div className="panel stack">
        <h2>Пригласить сотрудника</h2>
        <div className="form-row">
          <input placeholder="Логин" value={login} onChange={(e) => setLogin(e.target.value)} />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="administrator">Администратор</option>
            <option value="moderator">Модератор</option>
            <option value="support">Поддержка</option>
          </select>
          <button type="button" className="primary" disabled={busy} onClick={() => void invite()}>
            Создать приглашение
          </button>
        </div>
        {inviteToken && (
          <div className="stack">
            <p className="muted small">
              Токен показывается один раз. Передайте сотруднику безопасным каналом: он вводит его
              вместе с постоянным паролем на странице входа.
            </p>
            <div className="secret-box">{inviteToken}</div>
          </div>
        )}
      </div>
      <div className="panel">
        <div className="panel-head">
          <h2>Аккаунты</h2>
        </div>
        <DataTable
          columns={[
            { key: "login", label: "Логин" },
            {
              key: "role",
              label: "Роль",
              render: (row) => ROLE_LABEL[String(row.role)] || String(row.role),
            },
            {
              key: "disabled",
              label: "Доступ",
              render: (row) => (
                <span className={row.disabled ? "badge bad" : "badge ok"}>
                  {row.disabled ? "Отключён" : "Активен"}
                </span>
              ),
            },
            {
              key: "mfa",
              label: "MFA",
              render: (row) => (
                <span className={row.mfa ? "badge ok" : "badge warn"}>{row.mfa ? "включена" : "нет"}</span>
              ),
            },
            {
              key: "must_change_password",
              label: "Пароль",
              render: (row) => (
                <span className={row.must_change_password ? "badge warn" : "badge"}>
                  {row.must_change_password ? "временный" : "постоянный"}
                </span>
              ),
            },
          ]}
          rows={items as unknown as Record<string, unknown>[]}
          emptyText="Сотрудников нет."
          actions={(row) => {
            if (row.role === "owner" || row.id === undefined) return null;
            const id = String(row.id);
            return (
              <>
                {row.role !== "administrator" && (
                  <button
                    type="button"
                    className="ghost small"
                    disabled={busy}
                    onClick={() => {
                      if (
                        !window.confirm(
                          `Сделать сотрудника ${row.login} администратором? Он получит доступ к Premium, журналу и настройкам.`,
                        )
                      ) {
                        return;
                      }
                      void run(
                        () =>
                          api(`/staff/${id}/role`, me.csrf, {
                            method: "POST",
                            body: JSON.stringify({ role: "administrator" }),
                          }),
                        "Роль изменена на администратора.",
                      );
                    }}
                  >
                    Администратор
                  </button>
                )}
                {row.role !== "moderator" && (
                  <button
                    type="button"
                    className="ghost small"
                    disabled={busy}
                    onClick={() => {
                      if (!window.confirm(`Выдать сотруднику ${row.login} права модератора?`)) return;
                      void run(() => api(`/staff/${id}/role`, me.csrf, { method: "POST", body: JSON.stringify({ role: "moderator" }) }), "Роль изменена на модератора.");
                    }}
                  >
                    Модератор
                  </button>
                )}
                {row.role !== "support" && (
                  <button
                    type="button"
                    className="ghost small"
                    disabled={busy}
                    onClick={() => {
                      if (!window.confirm(`Сделать сотрудника ${row.login} поддержкой?`)) return;
                      void run(() => api(`/staff/${id}/role`, me.csrf, { method: "POST", body: JSON.stringify({ role: "support" }) }), "Роль изменена на поддержку.");
                    }}
                  >
                    Поддержка
                  </button>
                )}
                {row.disabled ? (
                  <button
                    type="button"
                    className="primary small"
                    disabled={busy}
                    onClick={() => void run(() => api(`/staff/${id}/enable`, me.csrf, { method: "POST" }), "Доступ восстановлен.")}
                  >
                    Включить
                  </button>
                ) : (
                  <button
                    type="button"
                    className="danger small"
                    disabled={busy}
                    onClick={() => {
                    if (!window.confirm(`Отключить доступ сотруднику ${row.login}? Все его сессии будут завершены.`)) return;
                    void run(() => api(`/staff/${id}/disable`, me.csrf, { method: "POST" }), "Доступ отключён, сессии завершены.");
                  }}
                  >
                    Отключить
                  </button>
                )}
              </>
            );
          }}
        />
      </div>
      <div className="panel">
        <div className="panel-head">
          <h2>Активные приглашения</h2>
        </div>
        {invites.length === 0 ? (
          <p className="muted">Активных приглашений нет.</p>
        ) : (
          <div className="stack">
            {invites.map((inviteRow) => (
              <div key={inviteRow.id} className="row" style={{ justifyContent: "space-between" }}>
                <span>
                  <strong>{inviteRow.login}</strong> · {ROLE_LABEL[inviteRow.role] || inviteRow.role}
                </span>
                <span className="muted small">
                  до {new Date(inviteRow.expires_at).toLocaleString("ru-RU")}
                </span>
                <button
                  type="button"
                  className="danger small"
                  disabled={busy}
                  onClick={() =>
                    void run(
                      () => api(`/staff/invites/${inviteRow.id}/revoke`, me.csrf, { method: "POST" }),
                      "Приглашение отозвано.",
                    )
                  }
                >
                  Отозвать
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
      <p className="muted small">
        Текущий статус аккаунта: <span className={statusBadgeClass(me.role)}>{roleLabel(me.role)}</span>
      </p>
    </section>
  );
}
