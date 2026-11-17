import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api, statusBadgeClass, statusLabel } from "../api/client";
import type { AuthState } from "../api/client";
import { DataTable } from "../components/DataTable";

type UserRow = {
  id: string;
  telegram_id: number;
  username: string | null;
  name: string | null;
  profile_types: string[];
  status: string;
  created_at: string | null;
  last_seen_at: string | null;
  premium: string | null;
  startups_count: number;
};

type UsersResponse = {
  items: UserRow[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  profile_types: string[];
};

type Restriction = {
  id: string;
  kind: string;
  reason: string;
  active: boolean;
  created_at: string | null;
};

type UserDetail = UserRow & {
  locale: string;
  startups: {
    id: string;
    name: string;
    review_state: string;
    revision: number;
    published_at: string | null;
    created_at?: string | null;
    archived?: boolean;
    premium_until?: string | null;
  }[];
  investors?: {
    id: string;
    display_name: string | null;
    review_status: string;
    investor_type: string;
    current_revision: number;
    created_at: string | null;
    archived: boolean;
    premium_until: string | null;
  }[];
  organizations_created?: {
    id: string;
    name: string;
    review_status: string;
    created_at: string | null;
    archived?: boolean;
    premium_until: string | null;
  }[];
  profile_limits?: {
    startup: { used: number; limit: number; available: number; plan_code: string | null; plan_until: string | null };
    investor: { used: number; limit: number; available: number; plan_code: string | null; plan_until: string | null };
    organization: { used: number; limit: number; available: number; plan_code: string | null; plan_until: string | null };
  };
  investor: {
    id: string;
    display_name: string | null;
    review_status: string;
    investor_type: string;
    current_revision: number;
  } | null;
  memberships: {
    membership_id: string;
    organization_id: string;
    organization: string;
    organization_status: string;
    status: string;
    mandate: boolean;
  }[];
  tickets: { id: string; number: string; subject: string; status: string }[];
  premium: {
    id: string;
    plan: string;
    status: string;
    owner_type: string;
    starts_at: string | null;
    expires_at: string | null;
  }[];
  restrictions: Restriction[];
  complaints: {
    id: string;
    number: string;
    target_type: string;
    status: string;
    reason: string;
    created_at: string | null;
  }[];
  moderation_history: {
    kind: string;
    title: string;
    review_id: string;
    revision: number;
    state: string;
    comment: string | null;
    submitted_at: string | null;
    decided_at: string | null;
  }[];
  admin_actions: { action: string; actor: string | null; reason: string | null; created_at: string }[];
  can_restrict: boolean;
};

type Filters = {
  q: string;
  status: string;
  profileType: string;
  sort: string;
  direction: string;
  page: number;
};

const STORAGE_KEY = "raundo-admin-users-view";

const DEFAULT_FILTERS: Filters = {
  q: "",
  status: "",
  profileType: "",
  sort: "created_at",
  direction: "desc",
  page: 1,
};

const PROFILE_LABELS: Record<string, string> = {
  founder: "Основатель",
  investor: "Инвестор",
  representative: "Представитель",
  viewer: "Зритель",
};

const ACTION_LABELS: Record<string, string> = {
  restrict: "Доступ ограничен",
  unrestrict: "Ограничение снято",
  approve: "Анкета одобрена",
  reject: "Анкета отклонена",
  request_changes: "Запрошены изменения",
  ticket_reply: "Ответ в поддержку",
  ticket_status: "Статус обращения изменён",
};

function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? "Административное действие";
}

const TARGET_LABELS: Record<string, string> = {
  startup: "Стартап",
  investor: "Инвестор",
  organization: "Организация",
  user: "Пользователь",
  other: "Другое",
};

const SORT_OPTIONS: { key: string; label: string }[] = [
  { key: "created_at", label: "По регистрации" },
  { key: "last_seen_at", label: "По активности" },
  { key: "name", label: "По имени" },
];

function readFilters(): Filters {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_FILTERS;
    const parsed = JSON.parse(raw) as Partial<Filters>;
    return {
      q: typeof parsed.q === "string" ? parsed.q : "",
      status: typeof parsed.status === "string" ? parsed.status : "",
      profileType: typeof parsed.profileType === "string" ? parsed.profileType : "",
      sort: typeof parsed.sort === "string" ? parsed.sort : "created_at",
      direction: parsed.direction === "asc" ? "asc" : "desc",
      page: typeof parsed.page === "number" && parsed.page > 0 ? parsed.page : 1,
    };
  } catch {
    return DEFAULT_FILTERS;
  }
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleDateString("ru-RU") : "—";
}

function formatDateTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("ru-RU") : "—";
}

function profileLabel(types: string[] | undefined): string {
  return (types || []).map((type) => PROFILE_LABELS[type] || type).join(", ") || "—";
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="field-key muted small">{label}</div>
      <div>{children}</div>
    </div>
  );
}

const PLAN_LABEL: Record<string, string> = {
  community_plus: "Community PLUS",
  startup_pro: "Startup PRO",
  investor_pro: "Investor PRO",
  organization_pro: "Organization PRO",
};

function planLabel(value: unknown): string {
  const code = String(value ?? "");
  return PLAN_LABEL[code] || "Premium";
}

export function UsersPage({
  me,
  focusId,
  onNavigate,
}: {
  me: AuthState;
  focusId?: string;
  onNavigate: (section: string, focusId?: string) => void;
}) {
  const [filters, setFilters] = useState<Filters>(readFilters);
  const [data, setData] = useState<UsersResponse | null>(null);
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [selectedId, setSelectedId] = useState<string | undefined>(focusId);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [restrictOpen, setRestrictOpen] = useState(false);
  const [restrictReason, setRestrictReason] = useState("");
  const [restrictConfirm, setRestrictConfirm] = useState(false);
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
    if (filters.profileType) params.set("profile_type", filters.profileType);
    params.set("sort", filters.sort);
    params.set("direction", filters.direction);
    try {
      const payload = await api<UsersResponse>(`/users?${params.toString()}`, me.csrf);
      if (request !== requestRef.current) return;
      setData(payload);
    } catch (e) {
      if (request === requestRef.current) {
        setError(e instanceof Error ? e.message : "Не удалось загрузить пользователей.");
      }
    }
  }, [filters, me.csrf]);

  const open = useCallback(
    async (id: string) => {
      setSelectedId(id);
      setError("");
      setNotice("");
      // Never carry a prepared restriction or its confirmation to another user.
      setRestrictOpen(false);
      setRestrictReason("");
      setRestrictConfirm(false);
      try {
        setDetail(await api<UserDetail>(`/users/${id}`, me.csrf));
        window.setTimeout(scrollToDetail, 0);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Пользователь недоступен.");
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

  async function submitRestriction(kind: "restrict" | "unrestrict") {
    if (!detail) return;
    const reason = restrictReason.trim();
    if (reason.length < 8) {
      setError("Укажите причину не короче 8 символов.");
      return;
    }
    if (!restrictConfirm) {
      setError("Подтвердите действие: оно сразу применяется в Telegram.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(`/users/${detail.id}/${kind}`, me.csrf, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      setRestrictOpen(false);
      setRestrictReason("");
      setRestrictConfirm(false);
      await open(detail.id);
      await load();
      setNotice(
        kind === "restrict"
          ? "Доступ ограничен. Уведомление поставлено в очередь доставки в Telegram."
          : "Ограничение снято. Уведомление поставлено в очередь доставки в Telegram.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Действие не выполнено.");
    } finally {
      setBusy(false);
    }
  }

  const canReview = ["owner", "administrator", "moderator"].includes(me.role);
  const canSupportView = ["owner", "administrator", "support"].includes(me.role);
  const activeRestriction = detail?.restrictions.find((item) => item.active) || null;
  const premiumActive = detail?.premium.filter((item) => item.status === "active") || [];

  return (
    <section className="stack">
      <div className="page-head">
        <div>
          <h1>Пользователи</h1>
          <p className="page-lead">
            Поиск по имени, username, Telegram ID и названию стартапа. В карточке — профиль,
            связанные объекты, история модерации и административные действия.
          </p>
        </div>
      </div>
      {error && <p className="error-text">{error}</p>}
      {notice && <p className="ok-text">{notice}</p>}

      <div className="panel stack">
        <div className="filter-bar">
          <input
            aria-label="Поиск пользователей"
            placeholder="Имя, username, Telegram ID или стартап"
            value={filters.q}
            onChange={(e) => patchFilters({ q: e.target.value })}
          />
          <select
            aria-label="Статус аккаунта"
            value={filters.status}
            onChange={(e) => patchFilters({ status: e.target.value })}
          >
            <option value="">Все статусы</option>
            <option value="active">Активен</option>
            <option value="restricted">Доступ ограничен</option>
          </select>
          <select
            aria-label="Тип профиля"
            value={filters.profileType}
            onChange={(e) => patchFilters({ profileType: e.target.value })}
          >
            <option value="">Все типы</option>
            {(data?.profile_types || []).map((type) => (
              <option key={type} value={type}>
                {PROFILE_LABELS[type] || type}
              </option>
            ))}
          </select>
          <select
            aria-label="Сортировка"
            value={filters.sort}
            onChange={(e) => patchFilters({ sort: e.target.value })}
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="ghost"
            onClick={() =>
              patchFilters({ direction: filters.direction === "asc" ? "desc" : "asc" })
            }
          >
            {filters.direction === "asc" ? "↑ По возрастанию" : "↓ По убыванию"}
          </button>
          <button
            type="button"
            className="ghost"
            onClick={() => {
              setFilters(DEFAULT_FILTERS);
              setRestrictOpen(false);
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
              { key: "name", label: "Имя", render: (row) => String(row.name || "—") },
              {
                key: "username",
                label: "Username",
                render: (row) => (row.username ? `@${row.username}` : "—"),
              },
              { key: "telegram_id", label: "Telegram ID" },
              {
                key: "profile_types",
                label: "Тип профиля",
                hideOnMobile: true,
                render: (row) => profileLabel((row.profile_types as string[]) || []),
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
                key: "created_at",
                label: "Регистрация",
                hideOnMobile: true,
                render: (row) => formatDate(row.created_at as string | null),
              },
              {
                key: "last_seen_at",
                label: "Активность",
                hideOnMobile: true,
                render: (row) => formatDate(row.last_seen_at as string | null),
              },
              {
                key: "premium",
                label: "Premium",
                hideOnMobile: true,
                render: (row) =>
                  row.premium ? <span className="badge info">{planLabel(row.premium)}</span> : "-",
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
            emptyText="Пользователи не найдены. Измените запрос или фильтры."
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

        <div className={detail || selectedId ? "panel stack" : "muted small"} ref={detailRef}>
          {!detail ? (
            <p className="muted">
              {selectedId ? "Загрузка карточки…" : "Выберите пользователя в таблице."}
            </p>
          ) : (
            <>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <h2>{detail.name || detail.username || `ID ${detail.telegram_id}`}</h2>
                  <p className="muted small">
                    {detail.username ? `@${detail.username} · ` : ""}Telegram ID{" "}
                    {detail.telegram_id}
                  </p>
                </div>
                <div className="row">
                  <span className={statusBadgeClass(detail.status)}>
                    {statusLabel(detail.status)}
                  </span>
                  {premiumActive.length > 0 && (
                    <span className="badge info">
                      Premium · {premiumActive.map((item) => planLabel(item.plan)).join(", ")}
                    </span>
                  )}
                </div>
              </div>

              <div className="field-grid">
                <Field label="Регистрация">{formatDateTime(detail.created_at)}</Field>
                <Field label="Последняя активность">
                  {formatDateTime(detail.last_seen_at)}
                </Field>
                <Field label="Тип профиля">{profileLabel(detail.profile_types)}</Field>
                <Field label="Язык">{detail.locale || "—"}</Field>
              </div>

              {activeRestriction && (
                <div className="callout danger">
                  <strong>Доступ ограничен</strong>
                  <p>{activeRestriction.reason}</p>
                  <p className="muted small">
                    Ограничение от {formatDateTime(activeRestriction.created_at)}
                  </p>
                </div>
              )}

              {detail.can_restrict && (
                <div className="danger-zone">
                  {!activeRestriction ? (
                    <>
                      <button
                        type="button"
                        className="danger small"
                        onClick={() => {
                          setRestrictOpen((value) => !value);
                          setRestrictConfirm(false);
                          setRestrictReason("");
                          setError("");
                        }}
                      >
                        Ограничить доступ
                      </button>
                      {restrictOpen && (
                        <div className="stack mt-12">
                          <p className="muted small">
                            Ограничение сразу блокирует разделы бота в Telegram. Поддержка и
                            команда /start останутся доступными.
                          </p>
                          <textarea
                            aria-label="Причина ограничения"
                            placeholder="Причина ограничения (видна пользователю)"
                            value={restrictReason}
                            onChange={(e) => setRestrictReason(e.target.value)}
                          />
                          <label className="check-row">
                            <input
                              type="checkbox"
                              checked={restrictConfirm}
                              onChange={(e) => setRestrictConfirm(e.target.checked)}
                            />
                            Подтверждаю ограничение и уведомление пользователя
                          </label>
                          <div className="actions">
                            <button
                              type="button"
                              className="danger"
                              disabled={busy || restrictReason.trim().length < 8 || !restrictConfirm}
                              onClick={() => void submitRestriction("restrict")}
                            >
                              Применить ограничение
                            </button>
                            <button
                              type="button"
                              className="ghost"
                              disabled={busy}
                              onClick={() => setRestrictOpen(false)}
                            >
                              Отмена
                            </button>
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="ghost small"
                        onClick={() => {
                          setRestrictOpen((value) => !value);
                          setRestrictConfirm(false);
                          setRestrictReason("");
                          setError("");
                        }}
                      >
                        Снять ограничение
                      </button>
                      {restrictOpen && (
                        <div className="stack mt-12">
                          <textarea
                            aria-label="Причина снятия ограничения"
                            placeholder="Причина снятия ограничения"
                            value={restrictReason}
                            onChange={(e) => setRestrictReason(e.target.value)}
                          />
                          <label className="check-row">
                            <input
                              type="checkbox"
                              checked={restrictConfirm}
                              onChange={(e) => setRestrictConfirm(e.target.checked)}
                            />
                            Подтверждаю снятие ограничения
                          </label>
                          <div className="actions">
                            <button
                              type="button"
                              className="primary"
                              disabled={busy || restrictReason.trim().length < 8 || !restrictConfirm}
                              onClick={() => void submitRestriction("unrestrict")}
                            >
                              Снять и уведомить
                            </button>
                            <button
                              type="button"
                              className="ghost"
                              disabled={busy}
                              onClick={() => setRestrictOpen(false)}
                            >
                              Отмена
                            </button>
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}

              <div>
                <h3>Стартапы</h3>
                {detail.startups.length === 0 ? (
                  <p className="muted small">Стартапов нет.</p>
                ) : (
                  <ul className="linked-list">
                    {detail.startups.map((startup) => (
                      <li key={startup.id}>
                        <div>
                          <strong>{startup.name}</strong>
                          <div className="muted small">
                            {statusLabel(startup.review_state)} · версия {startup.revision}
                            {startup.archived ? " · архив" : ""}
                            {startup.premium_until
                              ? ` · Startup PRO до ${new Date(startup.premium_until).toLocaleDateString("ru-RU")}`
                              : ""}
                          </div>
                        </div>
                        {canReview && (
                          <button
                            type="button"
                            className="ghost small"
                            onClick={() => onNavigate("moderation")}
                          >
                            К модерации
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3>Инвесторские профили</h3>
                {(() => {
                  const investors = detail.investors ?? (detail.investor ? [{
                    id: detail.investor.id,
                    display_name: detail.investor.display_name,
                    review_status: detail.investor.review_status,
                    investor_type: detail.investor.investor_type,
                    current_revision: detail.investor.current_revision,
                    created_at: null,
                    archived: false,
                    premium_until: null,
                  }] : []);
                  if (investors.length === 0) {
                    return <p className="muted small">Анкета не заполнена.</p>;
                  }
                  return (
                    <ul className="linked-list">
                      {investors.map((profile, index) => (
                        <li key={profile.id}>
                          <div>
                            <strong>{profile.display_name || `Профиль ${index + 1}`}</strong>
                            <div className="muted small">
                              {statusLabel(profile.review_status)} · версия{" "}
                              {profile.current_revision}
                              {profile.archived ? " · архив" : ""}
                              {profile.premium_until
                                ? ` · Investor PRO до ${new Date(profile.premium_until).toLocaleDateString("ru-RU")}`
                                : ""}
                            </div>
                          </div>
                          {canReview && (
                            <button
                              type="button"
                              className="ghost small"
                              onClick={() => onNavigate("moderation")}
                            >
                              К модерации
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  );
                })()}
              </div>

              <div>
                <h3>Организации</h3>
                {detail.memberships.length === 0 ? (
                  <p className="muted small">Нет заявленных организаций.</p>
                ) : (
                  <ul className="linked-list">
                    {detail.memberships.map((membership) => (
                      <li key={membership.membership_id}>
                        <div>
                          <strong>{membership.organization}</strong>
                          <div className="muted small">
                            Представитель · {statusLabel(membership.status)} · организация{" "}
                            {statusLabel(membership.organization_status)}
                            {membership.mandate ? " · прямое инвестирование" : ""}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="ghost small"
                          onClick={() => onNavigate("orgs", membership.organization_id)}
                        >
                          Открыть
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3>Созданные организации</h3>
                {!detail.organizations_created || detail.organizations_created.length === 0 ? (
                  <p className="muted small">Своих организаций нет.</p>
                ) : (
                  <ul className="linked-list">
                    {detail.organizations_created.map((org) => (
                      <li key={org.id}>
                        <div>
                          <strong>{org.name}</strong>
                          <div className="muted small">
                            {statusLabel(org.review_status)}
                            {org.archived ? " · архив" : ""}
                            {org.premium_until
                              ? ` · Organization PRO до ${new Date(org.premium_until).toLocaleDateString("ru-RU")}`
                              : ""}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="ghost small"
                          onClick={() => onNavigate("orgs", org.id)}
                        >
                          Открыть
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3>Лимиты профилей</h3>
                {!detail.profile_limits ? (
                  <p className="muted small">Нет данных о лимитах.</p>
                ) : (
                  <ul className="linked-list">
                    {([
                      ["Стартапы", detail.profile_limits.startup, "Startup PRO"],
                      ["Инвесторские профили", detail.profile_limits.investor, "Investor PRO"],
                      ["Организации", detail.profile_limits.organization, "Organization PRO"],
                    ] as const).map(([label, value, planName]) => (
                      <li key={label}>
                        <div>
                          <strong>{label}</strong>: {value.used} / {value.limit}
                          <div className="muted small">
                            {value.plan_code
                              ? `${planName} до ${value.plan_until ? new Date(value.plan_until).toLocaleDateString("ru-RU") : "—"}`
                              : "Бесплатный тариф"}
                          </div>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3>Premium-подписки</h3>
                {detail.premium.length === 0 ? (
                  <p className="muted small">Подписок нет.</p>
                ) : (
                  <ul className="linked-list">
                    {detail.premium.map((subscription) => (
                      <li key={subscription.id}>
                        <div>
                          <strong>{planLabel(subscription.plan)}</strong>
                          <div className="muted small">
                            {statusLabel(subscription.status)} · до{" "}
                            {formatDate(subscription.expires_at)}
                          </div>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3>История модерации</h3>
                {detail.moderation_history.length === 0 ? (
                  <p className="muted small">Записей нет.</p>
                ) : (
                  <ul className="linked-list">
                    {detail.moderation_history.map((entry) => (
                      <li key={`${entry.kind}-${entry.review_id}`}>
                        <div>
                          <strong>{entry.title}</strong>
                          <div className="muted small">
                            {entry.kind === "startup" ? "Стартап" : "Инвестор"} ·{" "}
                            {statusLabel(entry.state)} · версия {entry.revision} ·{" "}
                            {formatDateTime(entry.decided_at || entry.submitted_at)}
                          </div>
                          {entry.comment && <div className="small">{entry.comment}</div>}
                        </div>
                        {canReview && (
                          <button
                            type="button"
                            className="ghost small"
                            onClick={() => onNavigate("moderation", entry.review_id)}
                          >
                            Открыть
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3>Обращения в поддержку</h3>
                {detail.tickets.length === 0 ? (
                  <p className="muted small">Обращений нет.</p>
                ) : (
                  <ul className="linked-list">
                    {detail.tickets.map((ticket) => (
                      <li key={ticket.id}>
                        <div>
                          <strong>{ticket.number}</strong>
                          <div className="muted small">
                            {ticket.subject} · {statusLabel(ticket.status)}
                          </div>
                        </div>
                        {canSupportView && (
                          <button
                            type="button"
                            className="ghost small"
                            onClick={() => onNavigate("support", ticket.id)}
                          >
                            Открыть
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3>Жалобы</h3>
                {detail.complaints.length === 0 ? (
                  <p className="muted small">Жалоб нет.</p>
                ) : (
                  <ul className="linked-list">
                    {detail.complaints.map((complaint) => (
                      <li key={complaint.id}>
                        <div>
                          <strong>{complaint.number}</strong>
                          <div className="muted small">
                            {TARGET_LABELS[complaint.target_type] || complaint.target_type} ·{" "}
                            {statusLabel(complaint.status)} ·{" "}
                            {formatDate(complaint.created_at)}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="ghost small"
                          onClick={() => onNavigate("complaints", complaint.id)}
                        >
                          Открыть
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3>История административных действий</h3>
                {detail.admin_actions.length === 0 ? (
                  <p className="muted small">Действий не зафиксировано.</p>
                ) : (
                  <ul className="timeline">
                    {detail.admin_actions.map((action, index) => (
                      <li key={`${action.created_at}-${index}`}>
                        <span className="timeline-dot" aria-hidden="true" />
                        <div>
                          <strong>{actionLabel(action.action)}</strong>
                          <div className="muted small">
                            {action.actor || "система"} · {formatDateTime(action.created_at)}
                          </div>
                          {action.reason && <div className="small">{action.reason}</div>}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
