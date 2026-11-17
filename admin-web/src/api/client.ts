export type AuthState = {
  login: string;
  role: string;
  csrf: string;
  mustChangePassword?: boolean;
  mfaRequired?: boolean;
  mfaEnabled?: boolean;
  mfaSetupPending?: boolean;
};

export type SearchHit = {
  type: string;
  id: string;
  title: string;
  subtitle?: string;
  section: string;
};

async function readError(res: Response): Promise<string> {
  const raw = await res.text();
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    /* not JSON */
  }
  return raw || `Ошибка ${res.status}`;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(path: string, csrf: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method && init.method !== "GET") headers.set("x-csrf-token", csrf);
  if (init.body) headers.set("content-type", "application/json");
  const res = await fetch(`/api${path}`, { ...init, headers, credentials: "include" });
  if (!res.ok) {
    const error = new ApiError(await readError(res), res.status);
    // An expired/revoked session must return the shell to the login screen
    // instead of rendering "Ошибка" on a half-broken dashboard.
    // 401 here means "session expired" except for endpoints where it is a
    // domain answer (wrong MFA code). Those must not drop the whole shell.
    const domainErrors = ["/auth/login", "/auth/mfa/verify-login"];
    if (res.status === 401 && !domainErrors.includes(path)) {
      window.dispatchEvent(new Event("raundo:unauthorized"));
    }
    throw error;
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function login(loginName: string, password: string) {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ login: loginName, password }),
  });
  if (!res.ok) throw new Error(await readError(res));
  const body = await res.json();
  return {
    login: body.login as string | undefined,
    role: body.role as string | undefined,
    csrf: body.csrf as string | undefined,
    mfaRequired: Boolean(body.mfa_required),
    mustChangePassword: Boolean(body.must_change_password),
  };
}

export function statusBadgeClass(value: string): string {
  if (["approved", "accepted", "delivered", "resolved", "active", "ok"].includes(value)) {
    return "badge ok";
  }
  if (
    ["submitted", "pending", "processing", "retry_scheduled", "waiting_user", "awaiting_confirmation", "needs_help", "in_progress", "new", "draft", "claimed", "under_review", "in_review", "pending_changes"].includes(
      value,
    )
  ) {
    return "badge warn";
  }
  if (["rejected", "failed", "blocked", "revoked", "closed", "disabled", "suspended", "restricted", "archived", "dismissed", "cancelled", "canceled"].includes(value)) {
    return "badge bad";
  }
  return "badge info";
}

export const STATUS_LABELS: Record<string, string> = {
  approved: "Подтверждено",
  rejected: "Отклонено",
  changes_requested: "Нужны изменения",
  pending_changes: "Нужны изменения",
  submitted: "На проверке",
  under_review: "В работе",
  draft: "Черновик",
  archived: "В архиве",
  pending: "В очереди",
  processing: "Отправляется",
  retry_scheduled: "Повтор запланирован",
  accepted: "Доставлено",
  delivered: "Доставлено",
  failed: "Ошибка доставки",
  undeliverable: "Недоставляемо",
  not_applicable: "Не отправляется",
  new: "Новое",
  in_progress: "В работе",
  waiting_user: "Ожидает уточнения",
  awaiting_confirmation: "Ожидает подтверждения",
  needs_help: "Требуется помощь",
  resolved: "Решено",
  closed: "Закрыто",
  superseded: "Заменена",
  open: "Открыта",
  in_review: "Проверяется",
  dismissed: "Отклонена",
  restricted: "Доступ ограничен",
  blocked: "Заблокирован",
  claimed: "Заявлено",
  revoked: "Отозвано",
  active: "Активна",
  expired: "Истекла",
  suspended: "Приостановлено",
  unknown: "Состояние неизвестно",
  cancelled: "Отменена",
  canceled: "Отменена",
  test: "Тестовая",
  created: "Создан",
  paid: "Оплачен",
  refunded: "Возврат",
};

export const ROLE_LABELS: Record<string, string> = {
  owner: "Владелец",
  administrator: "Администратор",
  moderator: "Модератор",
  support: "Поддержка",
};

export function roleLabel(value: string): string {
  return ROLE_LABELS[value] ?? "Сотрудник";
}

export function statusLabel(value: string): string {
  return STATUS_LABELS[value] ?? "Неизвестный статус";
}
