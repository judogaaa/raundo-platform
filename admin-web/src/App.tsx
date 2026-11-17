import { useCallback, useEffect, useRef, useState } from "react";
import { api, login as apiLogin, type AuthState, type SearchHit } from "./api/client";
import { Layout } from "./components/Layout";
import { BrandLogo } from "./components/BrandLogo";
import { AnalyticsPage, type Analytics } from "./pages/AnalyticsPage";
import { CatalogPage } from "./pages/CatalogPage";
import { ComplaintsPage } from "./pages/ComplaintsPage";
import { ModerationPage } from "./pages/ModerationPage";
import { OrganizationsPage } from "./pages/OrganizationsPage";
import { OverviewPage, type Overview } from "./pages/OverviewPage";
import { SettingsPage } from "./pages/SettingsPage";
import { AuditPage, PremiumPage } from "./pages/SimpleTablePage";
import { StaffPage } from "./pages/StaffPage";
import { SupportPage } from "./pages/SupportPage";
import { UsersPage } from "./pages/UsersPage";

type QueueItem = {
  review_id: string;
  kind: string;
  title: string;
  state: string;
  revision: number;
  submitted_at: string;
};

const MOD = ["owner", "administrator", "moderator"];
const SUPPORT = ["owner", "administrator", "support"];
const OWNER = ["owner"];
const ADMIN = ["owner", "administrator"];

export function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem("raundo-admin-theme") || "light");
  const [me, setMe] = useState<AuthState | null>(null);
  const [section, setSection] = useState("overview");
  const [focusId, setFocusId] = useState<string | undefined>(undefined);
  const [loginName, setLoginName] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [mfaPending, setMfaPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [searchValue, setSearchValue] = useState("");
  const [inviteMode, setInviteMode] = useState(false);
  const [inviteToken, setInviteToken] = useState("");
  const [invitePassword, setInvitePassword] = useState("");
  const [inviteConfirm, setInviteConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);

  const [overview, setOverview] = useState<Overview | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const searchTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("raundo-admin-theme", theme);
  }, [theme]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        document.getElementById("global-search")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const restoreSession = useCallback(async () => {
    try {
      const res = await fetch("/api/auth/me", { credentials: "include" });
      if (!res.ok) return;
      const body = await res.json();
      if (!body.csrf) return;
      setMe({
        login: body.login,
        role: body.role,
        csrf: body.csrf,
        mustChangePassword: Boolean(body.must_change_password),
        mfaRequired: Boolean(body.mfa_enabled),
        mfaEnabled: Boolean(body.mfa_enabled),
        mfaSetupPending: Boolean(body.mfa_setup_pending),
      });
      setMfaPending(Boolean(body.mfa_pending));
    } catch {
      /* not signed in */
    }
  }, []);

  useEffect(() => {
    void restoreSession();
  }, [restoreSession]);

  useEffect(() => {
    const onUnauthorized = () => {
      setMe(null);
      setOverview(null);
      setQueue([]);
      setAnalytics(null);
      setMfaPending(false);
      setNotice("Сессия истекла. Войдите снова.");
    };
    window.addEventListener("raundo:unauthorized", onUnauthorized);
    return () => window.removeEventListener("raundo:unauthorized", onUnauthorized);
  }, []);

  const reload = useCallback(async () => {
    if (!me || mfaPending || me.mustChangePassword) return;
    try {
      const data = await api<Overview>("/overview", me.csrf);
      setOverview(data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
    }
    // Section data is independent: one failed call must not freeze the rest.
    if (section === "moderation") {
      try {
        const res = await api<{ items: QueueItem[] }>("/moderation/queue", me.csrf);
        setQueue(res.items);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Очередь модерации недоступна.");
      }
    } else if (section === "analytics") {
      try {
        setAnalytics(await api<Analytics>("/analytics", me.csrf));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Аналитика недоступна.");
      }
    }
  }, [me, mfaPending, section]);

  useEffect(() => {
    void reload();
  }, [reload]);

  function go(next: string, id?: string) {
    setSection(next);
    setFocusId(id);
    setSearchValue("");
    setSearchHits([]);
    setError("");
    setNotice("");
  }

  function onSearchChange(value: string) {
    setSearchValue(value);
    window.clearTimeout(searchTimer.current);
    if (!me || value.trim().length < 2) {
      setSearchHits([]);
      return;
    }
    searchTimer.current = window.setTimeout(() => {
      api<{ items: SearchHit[] }>(`/search?q=${encodeURIComponent(value.trim())}`, me.csrf)
        .then((data) => setSearchHits(data.items))
        .catch(() => setSearchHits([]));
    }, 220);
  }

  function pickHit(hit: SearchHit) {
    go(hit.section, hit.id);
  }

  async function signIn() {
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      const body = await apiLogin(loginName, password);
      if (!body.csrf || !body.login || !body.role) {
        setError("Ответ сервера неполный.");
        return;
      }
      setMe({
        login: body.login,
        role: body.role,
        csrf: body.csrf,
        mustChangePassword: body.mustChangePassword,
        mfaRequired: body.mfaRequired,
        mfaEnabled: body.mfaRequired,
      });
      setMfaPending(Boolean(body.mfaRequired));
      setPassword("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Неверный логин или пароль.");
    } finally {
      setBusy(false);
    }
  }

  async function acceptInvite() {
    if (busy) return;
    if (invitePassword.length < 10) {
      setError("Пароль должен быть не короче 10 символов.");
      return;
    }
    if (invitePassword !== inviteConfirm) {
      setError("Пароли не совпадают.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      const res = await api<{ login: string }>("/staff/accept", "", {
        method: "POST",
        body: JSON.stringify({ token: inviteToken.trim(), password: invitePassword }),
      });
      setLoginName(res.login);
      setInviteMode(false);
      setInviteToken("");
      setInvitePassword("");
      setInviteConfirm("");
      setNotice("Аккаунт создан. Войдите с новым паролем.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Приглашение не принято.");
    } finally {
      setBusy(false);
    }
  }

  async function submitMfa() {
    if (!me || busy) return;
    setError("");
    setBusy(true);
    try {
      await api("/auth/mfa/verify-login", me.csrf, {
        method: "POST",
        body: JSON.stringify({ code: mfaCode.trim() }),
      });
      setMfaCode("");
      setMfaPending(false);
      await restoreSession();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Неверный код подтверждения.");
    } finally {
      setBusy(false);
    }
  }

  async function changeTemporaryPassword() {
    if (!me || busy) return;
    if (newPassword.length < 10) {
      setError("Пароль должен быть не короче 10 символов.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("Пароли не совпадают.");
      return;
    }
    setError("");
    setBusy(true);
    try {
      await api("/auth/password", me.csrf, {
        method: "POST",
        body: JSON.stringify({ password: newPassword }),
      });
      setNewPassword("");
      setConfirmPassword("");
      await restoreSession();
      setNotice("Пароль обновлён.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Пароль не изменён.");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    if (me) {
      try {
        await api("/auth/logout", me.csrf, { method: "POST" });
      } catch {
        /* ignore */
      }
    }
    setMe(null);
    setOverview(null);
    setQueue([]);
    setAnalytics(null);
    setSection("overview");
    setMfaPending(false);
    setPassword("");
    setError("");
    setNotice("");
    setSearchValue("");
    setSearchHits([]);
  }

  if (me && mfaPending) {
    return (
      <main className="login panel">
        <h1>Подтверждение входа</h1>
        <p className="muted">
          Введите код из приложения-аутентификатора или одноразовый резервный код.
        </p>
        <input
          autoFocus
          placeholder="Код"
          value={mfaCode}
          onChange={(e) => setMfaCode(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void submitMfa()}
        />
        <button
          type="button"
          className="primary mt-12"
          disabled={busy || mfaCode.trim().length < 6}
          onClick={() => void submitMfa()}
        >
          Продолжить
        </button>
        <button type="button" className="ghost" onClick={() => void logout()}>
          Выйти
        </button>
        {error && <p className="error-text">{error}</p>}
      </main>
    );
  }

  if (!me) {
    return (
      <main className="login panel">
        <BrandLogo size={56} />
        <h1>RAUNDO Admin</h1>
        <p className="muted">Закрытая панель. Вход только для сотрудников RAUNDO.</p>
        <input
          placeholder="Логин"
          autoComplete="username"
          value={loginName}
          onChange={(e) => setLoginName(e.target.value)}
        />
        <input
          className="mt-8"
          type="password"
          placeholder="Пароль"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void signIn()}
        />
        <button
          type="button"
          className="primary mt-12"
          disabled={busy}
          onClick={() => void signIn()}
        >
          Войти
        </button>
        <button
          type="button"
          className="ghost mt-8"
          disabled={busy}
          onClick={() => {
            setInviteMode((value) => !value);
            setError("");
          }}
        >
          {inviteMode ? "У меня есть логин" : "Принять приглашение"}
        </button>
        {inviteMode && (
          <>
            <input
              className="mt-8"
              placeholder="Код приглашения"
              value={inviteToken}
              onChange={(e) => setInviteToken(e.target.value)}
            />
            <input
              className="mt-8"
              type="password"
              autoComplete="new-password"
              placeholder="Новый пароль (от 10 символов)"
              value={invitePassword}
              onChange={(e) => setInvitePassword(e.target.value)}
            />
            <input
              className="mt-8"
              type="password"
              autoComplete="new-password"
              placeholder="Повторите пароль"
              value={inviteConfirm}
              onChange={(e) => setInviteConfirm(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void acceptInvite()}
            />
            <button
              type="button"
              className="primary mt-8"
              disabled={busy || !inviteToken.trim() || invitePassword.length < 10}
              onClick={() => void acceptInvite()}
            >
              Создать аккаунт
            </button>
          </>
        )}
        {notice && <p className="ok-text">{notice}</p>}
        {error && <p className="error-text">{error}</p>}
      </main>
    );
  }

  if (me.mustChangePassword) {
    return (
      <main className="login panel">
        <h1>Смена временного пароля</h1>
        <p className="muted">
          До смены пароля панель недоступна. Придумайте постоянный пароль не короче 10 символов.
        </p>
        <input
          type="password"
          placeholder="Новый пароль"
          autoComplete="new-password"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
        />
        <input
          type="password"
          placeholder="Повторите пароль"
          autoComplete="new-password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void changeTemporaryPassword()}
        />
        <button
          type="button"
          className="primary mt-12"
          disabled={busy}
          onClick={() => void changeTemporaryPassword()}
        >
          Сохранить пароль
        </button>
        <button type="button" className="ghost" onClick={() => void logout()}>
          Выйти
        </button>
        {error && <p className="error-text">{error}</p>}
      </main>
    );
  }

  const counts: Record<string, number> = overview
    ? {
        moderation: overview.pending_startups + overview.pending_investors,
        support: overview.open_tickets,
        complaints: overview.open_complaints,
        orgs: overview.pending_organizations + overview.pending_representatives,
      }
    : {};

  const canMod = MOD.includes(me.role);
  const canSupport = SUPPORT.includes(me.role);
  const canOwner = OWNER.includes(me.role);
  const canAdmin = ADMIN.includes(me.role);

  let content;
  if (section === "overview") {
    content = <OverviewPage data={overview} role={me.role} onNavigate={go} />;
  } else if (section === "catalog" && (canMod || canSupport)) {
    content = <CatalogPage csrf={me.csrf} onNavigate={go} />;
  } else if (section === "moderation" && canMod) {
    content = (
      <ModerationPage
        key={focusId || "queue"}
        items={queue}
        me={me}
        focusId={focusId}
        onReload={() => void reload()}
      />
    );
  } else if (section === "support" && canSupport) {
    content = (
      <SupportPage
        key={focusId || "inbox"}
        me={me}
        focusId={focusId}
        onChanged={() => void reload()}
      />
    );
  } else if (section === "users" && (canMod || canSupport)) {
    content = <UsersPage key={focusId || "users"} me={me} focusId={focusId} onNavigate={go} />;
  } else if (section === "orgs" && (canMod || canSupport)) {
    content = (
      <OrganizationsPage
        key={focusId || "orgs"}
        me={me}
        canReview={canMod}
        focusId={focusId}
        onChanged={() => void reload()}
      />
    );
  } else if (section === "complaints" && (canMod || canSupport)) {
    content = (
      <ComplaintsPage
        key={focusId || "complaints"}
        me={me}
        focusId={focusId}
        onNavigate={go}
        onChanged={() => void reload()}
      />
    );
  } else if (section === "premium" && canAdmin) {
    content = <PremiumPage csrf={me.csrf} />;
  } else if (section === "analytics") {
    content = <AnalyticsPage data={analytics} />;
  } else if (section === "staff" && canOwner) {
    content = <StaffPage me={me} />;
  } else if (section === "audit" && canAdmin) {
    content = <AuditPage csrf={me.csrf} />;
  } else if (section === "settings") {
    content = <SettingsPage me={me} authEnabled={canAdmin} onRefreshAuth={restoreSession} />;
  } else {
    content = <p className="error-text">Раздел недоступен для вашей роли.</p>;
  }

  return (
    <Layout
      section={section}
      onSection={go}
      theme={theme}
      onTheme={() => setTheme(theme === "light" ? "dark" : "light")}
      account={me.login}
      role={me.role}
      onLogout={() => void logout()}
      counts={counts}
      searchValue={searchValue}
      onSearchChange={onSearchChange}
      searchHits={searchHits}
      onPickHit={pickHit}
    >
      {notice && <p className="ok-text">{notice}</p>}
      {error && <p className="error-text">{error}</p>}
      {content}
    </Layout>
  );
}
