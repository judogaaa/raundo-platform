import { useCallback, useEffect, useState } from "react";
import QRCode from "qrcode";
import { api, roleLabel, statusBadgeClass, statusLabel } from "../api/client";
import type { AuthState } from "../api/client";
import { DataTable } from "../components/DataTable";

type Session = { id: string; expires_at: string; current: boolean; mfa_verified: boolean };
type Outbox = {
  id: string;
  kind: string;
  state: string;
  attempts: number;
  last_error: string | null;
  next_attempt_at: string | null;
  created_at: string;
};

const EVENT_LABEL: Record<string, string> = {
  "user.consent_accepted": "Согласие принято",
  "user.restricted": "Доступ ограничен",
  "user.unrestricted": "Доступ восстановлен",
  "startup.submitted": "Заявка стартапа",
  "startup.approved": "Стартап одобрен",
  "startup.rejected": "Стартап отклонён",
  "startup.changes_requested": "Стартап: нужны правки",
  "investor.submitted": "Заявка инвестора",
  "investor.approved": "Инвестор одобрен",
  "investor.rejected": "Инвестор отклонён",
  "investor.changes_requested": "Инвестор: нужны правки",
  "contact.requested": "Запрос на знакомство",
  "contact.accepted": "Знакомство принято",
  "contact.declined": "Знакомство отклонено",
  "support.reply": "Ответ поддержки",
  "complaint.resolved": "Жалоба рассмотрена",
  "complaint.dismissed": "Жалоба закрыта",
};

function eventLabel(kind: string): string {
  return EVENT_LABEL[kind] || "Уведомление";
}

export function SettingsPage({
  me,
  authEnabled,
  onRefreshAuth,
}: {
  me: AuthState;
  authEnabled: boolean;
  onRefreshAuth: () => Promise<void>;
}) {
  const [newPass, setNewPass] = useState("");
  const [confirmPass, setConfirmPass] = useState("");
  const [currentPass, setCurrentPass] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [setup, setSetup] = useState<{ otpauth_uri: string; secret: string } | null>(null);
  const [qr, setQr] = useState("");
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [outbox, setOutbox] = useState<Outbox[]>([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [disableOpen, setDisableOpen] = useState(false);
  const [disableCode, setDisableCode] = useState("");
  const mfaEnabled = Boolean(me.mfaEnabled ?? me.mfaRequired);

  const loadSessions = useCallback(async () => {
    const data = await api<{ items: Session[] }>("/auth/sessions", me.csrf);
    setSessions(data.items);
  }, [me.csrf]);

  const loadOutbox = useCallback(async () => {
    if (!authEnabled) return;
    try {
      const data = await api<{ items: Outbox[] }>("/outbox", me.csrf);
      setOutbox(data.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Очередь уведомлений недоступна.");
    }
  }, [authEnabled, me.csrf]);

  useEffect(() => {
    void loadSessions().catch(() => undefined);
    void loadOutbox();
  }, [loadSessions, loadOutbox]);

  async function run(fn: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError("");
    setMsg("");
    try {
      await fn();
      setMsg(success);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Операция не выполнена.");
    } finally {
      setBusy(false);
    }
  }

  async function changePassword() {
    if (newPass.length < 10) {
      setError("Пароль должен быть не короче 10 символов.");
      return;
    }
    if (newPass !== confirmPass) {
      setError("Пароли не совпадают.");
      return;
    }
    if (!me.mustChangePassword && !currentPass) {
      setError("Введите текущий пароль.");
      return;
    }
    await run(async () => {
      await api("/auth/password", me.csrf, {
        method: "POST",
        body: JSON.stringify({
          password: newPass,
          current_password: me.mustChangePassword ? undefined : currentPass,
        }),
      });
      setNewPass("");
      setConfirmPass("");
      setCurrentPass("");
    }, "Пароль обновлён.");
  }

  async function revokeSession(sessionId: string) {
    if (!window.confirm("Завершить эту сессию? Устройство потребует повторный вход.")) return;
    await run(async () => {
      await api(`/auth/sessions/${sessionId}/revoke`, me.csrf, { method: "POST" });
      await loadSessions();
    }, "Сессия завершена.");
  }

  async function revokeAllSessions() {
    if (
      !window.confirm(
        "Завершить все другие сессии? Текущая сессия останется активной.",
      )
    ) {
      return;
    }
    await run(async () => {
      await api("/auth/sessions/revoke-all", me.csrf, { method: "POST" });
      await loadSessions();
    }, "Другие сессии завершены.");
  }

  async function showSetup(res: { otpauth_uri: string; secret: string }) {
    setSetup(res);
    setBackupCodes([]);
    try {
      setQr(await QRCode.toDataURL(res.otpauth_uri, { margin: 1, width: 220 }));
    } catch {
      setQr("");
    }
  }

  const resumeSetup = useCallback(async () => {
    try {
      const res = await api<{ otpauth_uri: string; secret: string }>(
        "/auth/mfa/setup",
        me.csrf,
        { method: "POST" },
      );
      await showSetup(res);
    } catch {
      /* pending setup may have been cleared elsewhere */
    }
  }, [me.csrf]);

  useEffect(() => {
    if (!mfaEnabled && me.mfaSetupPending) void resumeSetup();
  }, [mfaEnabled, me.mfaSetupPending, resumeSetup]);

  async function setupMfa() {
    await run(async () => {
      const res = await api<{ otpauth_uri: string; secret: string }>("/auth/mfa/setup", me.csrf, {
        method: "POST",
      });
      await showSetup(res);
    }, "Отсканируйте QR-код и введите код из приложения.");
  }

  async function enableMfa() {
    await run(async () => {
      const res = await api<{ backup_codes: string[] }>("/auth/mfa/enable", me.csrf, {
        method: "POST",
        body: JSON.stringify({ code: mfaCode.trim() }),
      });
      setBackupCodes(res.backup_codes);
      setMfaCode("");
      setSetup(null);
      setQr("");
      await onRefreshAuth();
    }, "MFA включена. Сохраните резервные коды в надёжном месте.");
  }

  async function disableMfa() {
    await run(async () => {
      await api("/auth/mfa/disable", me.csrf, {
        method: "POST",
        body: JSON.stringify({ code: disableCode.trim() }),
      });
      setDisableCode("");
      setDisableOpen(false);
      setBackupCodes([]);
      await onRefreshAuth();
    }, "MFA отключена.");
  }

  async function copyText(value: string, success: string) {
    try {
      await navigator.clipboard.writeText(value);
      setMsg(success);
      setError("");
    } catch {
      setError("Не удалось скопировать. Выделите текст вручную.");
    }
  }

  async function retry(eventId: string) {
    await run(async () => {
      await api(`/outbox/${eventId}/retry`, me.csrf, { method: "POST" });
      await loadOutbox();
    }, "Событие возвращено в очередь.");
  }

  return (
    <section className="stack">
      <div className="page-head">
        <div>
          <h1>Настройки</h1>
          <p className="page-lead">
            Пароль, двухфакторная защита, активные сессии и очередь доставки уведомлений.
          </p>
        </div>
        <div className="row">
          <span className={statusBadgeClass(me.role)}>{roleLabel(me.role)}</span>
          <span className={mfaEnabled ? "badge ok" : "badge warn"}>
            MFA {mfaEnabled ? "включена" : "выключена"}
          </span>
        </div>
      </div>
      {error && <p className="error-text">{error}</p>}
      {msg && <p className="ok-text">{msg}</p>}
      <div className="split">
        <div className="stack">
          <div className="panel stack">
            <h2>Пароль</h2>
            <p className="muted small">
              Не короче 10 символов. Временный пароль из приглашения нужно сменить до начала работы.
            </p>
            <input
              type="password"
              placeholder="Текущий пароль"
              autoComplete="current-password"
              value={currentPass}
              onChange={(e) => setCurrentPass(e.target.value)}
            />
            <input
              type="password"
              placeholder="Новый пароль"
              autoComplete="new-password"
              value={newPass}
              onChange={(e) => setNewPass(e.target.value)}
            />
            <input
              type="password"
              placeholder="Повторите новый пароль"
              autoComplete="new-password"
              value={confirmPass}
              onChange={(e) => setConfirmPass(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void changePassword()}
            />
            <button
              type="button"
              className="primary"
              disabled={busy || newPass.length < 10 || confirmPass.length < 10}
              onClick={() => void changePassword()}
            >
              Сменить пароль
            </button>
          </div>
          <div className="panel stack">
            <div className="panel-head">
              <h2>Двухфакторная аутентификация</h2>
              <span className={mfaEnabled ? "badge ok" : "badge warn"}>
                {mfaEnabled ? "включена" : "выключена"}
              </span>
            </div>
            {mfaEnabled ? (
              <>
                <p className="muted small">
                  Двухфакторная аутентификация включена: при входе потребуется код из
                  приложения-аутентификатора или резервный код.
                </p>
                <button
                  type="button"
                  className="danger"
                  disabled={busy}
                  onClick={() => {
                    setError("");
                    setMsg("");
                    setDisableOpen(true);
                  }}
                >
                  Отключить MFA
                </button>
              </>
            ) : (
              <>
                <p className="muted small">
                  Защитите административный аккаунт дополнительным кодом при входе. Подойдёт
                  любое TOTP-приложение: 1Password, Google Authenticator, Aegis.
                </p>
                {!setup && (
                  <button
                    type="button"
                    className="primary"
                    disabled={busy}
                    onClick={() => void setupMfa()}
                  >
                    Включить MFA
                  </button>
                )}
                {setup && (
                  <div className="stack">
                    {qr && (
                      <img className="mfa-qr" src={qr} alt="QR-код для приложения-аутентификатора" />
                    )}
                    <p className="muted small">Если QR не считывается, введите ключ вручную:</p>
                    <div className="secret-box" data-testid="mfa-secret">
                      {setup.secret}
                    </div>
                    <button
                      type="button"
                      className="ghost small"
                      onClick={() => void copyText(setup.secret, "Ключ скопирован.")}
                    >
                      Скопировать ключ
                    </button>
                    <input
                      placeholder="Шестизначный код из приложения"
                      autoComplete="one-time-code"
                      value={mfaCode}
                      onChange={(e) => setMfaCode(e.target.value)}
                    />
                    <button
                      type="button"
                      className="primary"
                      disabled={busy || mfaCode.trim().length < 6}
                      onClick={() => void enableMfa()}
                    >
                      Подтвердить и включить
                    </button>
                  </div>
                )}
              </>
            )}
            {backupCodes.length > 0 && (
              <div className="stack mt-8">
                <p className="ok-text">
                  Резервные коды показываются один раз. Каждый код одноразовый: сохраните их
                  в надёжном месте.
                </p>
                <div className="codes-grid">
                  {backupCodes.map((code) => (
                    <span key={code}>{code}</span>
                  ))}
                </div>
                <button
                  type="button"
                  className="ghost small"
                  onClick={() => void copyText(backupCodes.join("\n"), "Резервные коды скопированы.")}
                >
                  Скопировать коды
                </button>
              </div>
            )}
          </div>
        </div>
        <div className="stack">
          <div className="panel stack">
            <div className="panel-head">
              <h2>Сессии</h2>
              <button type="button" className="ghost small" onClick={() => void loadSessions()}>
                Обновить
              </button>
            </div>
            {sessions.length === 0 ? (
              <p className="muted">Нет активных сессий.</p>
            ) : (
              <div className="stack">
                {sessions.map((s) => (
                  <div key={s.id} className="row" style={{ justifyContent: "space-between" }}>
                    <span>
                      <span className={s.current ? "badge ok" : "badge"}>
                        {s.current ? "Текущая" : "Сессия"}
                      </span>
                    </span>
                    <span className="muted small">
                      до {new Date(s.expires_at).toLocaleString("ru-RU")}
                    </span>
                    <span className={s.mfa_verified ? "badge ok" : "badge warn"}>
                      {s.mfa_verified ? "MFA" : "без MFA"}
                    </span>
                    {!s.current && (
                      <button
                        type="button"
                        className="ghost small"
                        disabled={busy}
                        onClick={() => void revokeSession(s.id)}
                      >
                        Завершить
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
          {authEnabled && me.role === "owner" && (
            <div className="panel">
              <div className="panel-head">
                <h2>Очередь уведомлений</h2>
                <button type="button" className="ghost small" onClick={() => void loadOutbox()}>
                  Обновить
                </button>
              </div>
              <DataTable
                columns={[
                  {
                    key: "kind",
                    label: "Тип",
                    render: (row) => eventLabel(String(row.kind)),
                  },
                  {
                    key: "state",
                    label: "Состояние",
                    render: (row) => (
                      <span className={statusBadgeClass(String(row.state))}>
                        {statusLabel(String(row.state))}
                      </span>
                    ),
                  },
                  { key: "attempts", label: "Попыток" },
                  { key: "last_error", label: "Последняя ошибка" },
                  {
                    key: "created_at",
                    label: "Создано",
                    render: (row) =>
                      row.created_at ? new Date(String(row.created_at)).toLocaleString("ru-RU") : "—",
                  },
                ]}
                rows={outbox as unknown as Record<string, unknown>[]}
                emptyText="Очередь пуста."
                actions={(row) => (
                  <button
                    type="button"
                    className="ghost small"
                    disabled={busy || !["failed", "retry_scheduled", "pending"].includes(String(row.state))}
                    onClick={() => void retry(String(row.id))}
                  >
                    Повторить
                  </button>
                )}
              />
            </div>
          )}
        </div>
      </div>
      {disableOpen && (
        <div
          className="lightbox"
          role="dialog"
          aria-modal="true"
          aria-label="Отключение двухфакторной аутентификации"
          onClick={() => setDisableOpen(false)}
        >
          <div className="lightbox-inner panel stack" onClick={(e) => e.stopPropagation()}>
            <h2>Отключить MFA?</h2>
            <p className="muted small">
              Подтвердите личность действующим кодом из приложения или резервным кодом. После
              отключения старые ключи и резервные коды перестанут работать.
            </p>
            <input
              placeholder="Код из приложения или резервный код"
              autoComplete="one-time-code"
              value={disableCode}
              onChange={(e) => setDisableCode(e.target.value)}
            />
            {error && <p className="error-text">{error}</p>}
            <div className="actions">
              <button
                type="button"
                className="danger"
                disabled={busy || disableCode.trim().length < 6}
                onClick={() => void disableMfa()}
              >
                Подтвердить отключение
              </button>
              <button
                type="button"
                className="ghost"
                disabled={busy}
                onClick={() => setDisableOpen(false)}
              >
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
