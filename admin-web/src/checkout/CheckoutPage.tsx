import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import logo from "./logo.png";

type Checkout = {
  order_number: string;
  status: string;
  plan: {
    code: string;
    title: string;
    audience: string;
    benefits: string[];
    period: string;
    hours: number;
  };
  amount: { minor: number; currency: string; label: string };
  mode: "demo" | "provider_test" | "live";
  is_test: boolean;
  consent: { required: boolean; version: string; accepted: boolean };
  legal: {
    terms_url: string;
    privacy_url: string;
    seller: { name: string; inn: string | null; contact: string | null } | null;
    refund_note: string;
  };
  card_available: boolean;
  stars_available: boolean;
  expires_at: string;
  paid_until?: string | null;
};

type View = "loading" | "checkout" | "pending" | "success" | "failed" | "expired" | "demo" | "error";

const TELEGRAM_URL = "https://t.me/RaundoAppBot";

function tokenFromLocation(): string {
  const match = window.location.pathname.match(/\/checkout\/([^/?#]+)/);
  if (match) return decodeURIComponent(match[1]);
  return new URLSearchParams(window.location.search).get("token") || "";
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("ru-RU", { day: "2-digit", month: "long", year: "numeric" });
}

function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

export function CheckoutPage() {
  const token = useMemo(tokenFromLocation, []);
  const [data, setData] = useState<Checkout | null>(null);
  const [view, setView] = useState<View>("loading");
  const [consent, setConsent] = useState(false);
  const [paying, setPaying] = useState(false);
  const [error, setError] = useState("");
  const [demoMessage, setDemoMessage] = useState("");
  const pollTimer = useRef<number | undefined>(undefined);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = undefined;
    }
  }, []);

  const load = useCallback(async () => {
    setError("");
    if (!token) {
      setView("expired");
      return;
    }
    try {
      const res = await fetch(`/api/public/checkout/${encodeURIComponent(token)}`, {
        headers: { accept: "application/json" },
        cache: "no-store",
      });
      if (res.status === 404 || res.status === 410) {
        setView("expired");
        return;
      }
      if (!res.ok) {
        setView("error");
        return;
      }
      const payload = (await res.json()) as Checkout;
      setData(payload);
      if (payload.status === "paid") {
        setView("success");
        stopPolling();
        return;
      }
      if (payload.status === "canceled") {
        setView("failed");
        stopPolling();
        return;
      }
      if (payload.status === "expired") {
        setView("expired");
        stopPolling();
        return;
      }
      if (payload.mode === "demo" && payload.status === "pending") {
        setDemoMessage(
          "Тестовый режим RAUNDO: реальная оплата не выполняется, подписка не активируется.",
        );
        setView("demo");
        return;
      }
      if (payload.status === "pending" || new URLSearchParams(window.location.search).has("returned")) {
        setView("pending");
        return;
      }
      setView("checkout");
    } catch {
      setView("error");
    }
  }, [token, stopPolling]);

  useEffect(() => {
    void load();
    return () => stopPolling();
  }, [load, stopPolling]);

  useEffect(() => {
    if (view !== "pending") {
      stopPolling();
      return;
    }
    // Real backend state only: no timer ever fakes a success.
    let attempts = 0;
    pollTimer.current = window.setInterval(async () => {
      attempts += 1;
      try {
        const res = await fetch(`/api/public/checkout/${encodeURIComponent(token)}/status`, {
          headers: { accept: "application/json" },
          cache: "no-store",
        });
        if (!res.ok) return;
        const payload = (await res.json()) as Checkout;
        setData(payload);
        if (payload.mode === "demo" && payload.status === "pending") {
          setDemoMessage(
            "Тестовый режим RAUNDO: реальная оплата не выполняется, подписка не активируется.",
          );
          setView("demo");
          stopPolling();
        } else if (payload.status === "paid") {
          setView("success");
          stopPolling();
        } else if (payload.status === "canceled") {
          setView("failed");
          stopPolling();
        } else if (payload.status === "expired") {
          setView("expired");
          stopPolling();
        }
      } catch {
        /* transient network error: keep polling */
      }
      if (attempts >= 40) stopPolling();
    }, 3000);
    return () => stopPolling();
  }, [view, token, stopPolling]);

  async function pay() {
    if (!consent || paying || !data) return;
    setPaying(true);
    setError("");
    try {
      const res = await fetch(`/api/public/checkout/${encodeURIComponent(token)}/pay`, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ consent: true }),
      });
      const payload = (await res.json().catch(() => ({}))) as {
        status?: string;
        confirmation_url?: string;
        message?: string;
        detail?: string;
      };
      if (res.status === 410) {
        setView("expired");
        return;
      }
      if (!res.ok) {
        setError(payload.detail || "Не удалось начать оплату. Попробуйте ещё раз.");
        return;
      }
      if (payload.status === "paid") {
        await load();
        return;
      }
      if (payload.status === "demo") {
        setDemoMessage(payload.message || "Тестовый режим: оплата не выполняется.");
        setView("demo");
        return;
      }
      if (payload.confirmation_url) {
        window.location.assign(payload.confirmation_url);
        return;
      }
      setView("pending");
    } catch {
      setError("Сеть недоступна. Проверьте соединение и попробуйте снова.");
    } finally {
      setPaying(false);
    }
  }

  const brand = (
    <div className="brand">
      <img className="brand-logo" src={logo} alt="RAUNDO" />
      <div>
        <div className="brand-name">RAUNDO</div>
        <div className="brand-caption">Оформление подписки</div>
      </div>
    </div>
  );

  const footer = (
    <footer className="legal">
      {data?.legal.seller ? (
        <p>
          Продавец: {data.legal.seller.name}
          {data.legal.seller.inn ? ` · ИНН ${data.legal.seller.inn}` : ""}
          {data.legal.seller.contact ? ` · ${data.legal.seller.contact}` : ""}
        </p>
      ) : (
        <p>Реквизиты продавца будут опубликованы до начала продаж.</p>
      )}
      <p>{data?.legal.refund_note || "Возврат рассматривается поддержкой RAUNDO."}</p>
      <p className="legal-links">
        <a href="/legal/terms" target="_blank" rel="noopener noreferrer">
          Соглашение
        </a>
        <span aria-hidden="true">·</span>
        <a href="/legal/offer" target="_blank" rel="noopener noreferrer">
          Оферта
        </a>
        <span aria-hidden="true">·</span>
        <a href="/legal/privacy" target="_blank" rel="noopener noreferrer">
          Конфиденциальность
        </a>
        <span aria-hidden="true">·</span>
        <a href="/legal/payments" target="_blank" rel="noopener noreferrer">
          Оплата и возвраты
        </a>
        <span aria-hidden="true">·</span>
        <a href={TELEGRAM_URL} target="_blank" rel="noopener noreferrer">
          Поддержка
        </a>
      </p>
      <p>
        <a className="back-link" href={TELEGRAM_URL}>
          ← Вернуться в Telegram
        </a>
      </p>
    </footer>
  );

  if (view === "loading") {
    return (
      <main className="page">
        <div className="shell">
          {brand}
          <section className="card state-card">
            <Spinner />
            <p className="muted">Загружаем заказ…</p>
          </section>
        </div>
      </main>
    );
  }

  if (view === "expired") {
    return (
      <main className="page">
        <div className="shell">
          {brand}
          <section className="card state-card">
            <div className="state-icon">⌛</div>
            <h1>Ссылка больше не действует</h1>
            <p className="muted">Создайте новый заказ в Telegram — это займёт несколько секунд.</p>
            <a className="button button-secondary" href={TELEGRAM_URL}>
              Вернуться в RAUNDO
            </a>
          </section>
          {footer}
        </div>
      </main>
    );
  }

  if (view === "error") {
    return (
      <main className="page">
        <div className="shell">
          {brand}
          <section className="card state-card">
            <div className="state-icon">⚠️</div>
            <h1>Не удалось открыть оплату</h1>
            <p className="muted">Проверьте соединение и попробуйте снова.</p>
            <button type="button" className="button button-primary" onClick={() => void load()}>
              Повторить
            </button>
            <a className="button button-secondary" href={TELEGRAM_URL}>
              Вернуться в RAUNDO
            </a>
          </section>
          {footer}
        </div>
      </main>
    );
  }

  if (view === "pending") {
    return (
      <main className="page">
        <div className="shell">
          {brand}
          <section className="card state-card">
            <Spinner />
            <h1>Проверяем оплату</h1>
            <p className="muted">
              Обычно это занимает немного времени. Не закрывайте страницу, если хотите увидеть
              результат здесь.
            </p>
            <a className="button button-secondary" href={TELEGRAM_URL}>
              Вернуться в RAUNDO
            </a>
          </section>
          {footer}
        </div>
      </main>
    );
  }

  if (view === "success") {
    return (
      <main className="page">
        <div className="shell">
          {brand}
          <section className="card state-card">
            <div className="state-icon state-icon-success">✓</div>
            <h1>Оплата прошла</h1>
            <p className="muted">
              Подписка {data?.plan.title || "Premium"} активирована
              {data?.paid_until ? ` до ${formatDate(data.paid_until)}` : ""}.
            </p>
            <div className="summary compact">
              <div>
                <span className="muted">Тариф</span>
                <strong>{data?.plan.title}</strong>
              </div>
              <div>
                <span className="muted">Период</span>
                <strong>{data?.plan.period}</strong>
              </div>
            </div>
            <a className="button button-primary" href={TELEGRAM_URL}>
              Вернуться в RAUNDO
            </a>
          </section>
          {footer}
        </div>
      </main>
    );
  }

  if (view === "failed") {
    return (
      <main className="page">
        <div className="shell">
          {brand}
          <section className="card state-card">
            <div className="state-icon">×</div>
            <h1>Оплата не завершена</h1>
            <p className="muted">
              Деньги не списаны. Можно вернуться в Telegram и создать новый заказ.
            </p>
            <a className="button button-primary" href={TELEGRAM_URL}>
              Вернуться в RAUNDO
            </a>
          </section>
          {footer}
        </div>
      </main>
    );
  }

  if (view === "demo") {
    return (
      <main className="page">
        <div className="shell">
          {brand}
          <section className="card state-card">
            <div className="state-icon">🧪</div>
            <h1>Тестовый режим</h1>
            <p className="muted">{demoMessage}</p>
            <a className="button button-secondary" href={TELEGRAM_URL}>
              Вернуться в RAUNDO
            </a>
          </section>
          {footer}
        </div>
      </main>
    );
  }

  return (
    <main className="page">
      <div className="shell">
        {brand}
        <section className="card checkout-card">
          <header className="plan-head">
            <h1>{data?.plan.title || "Premium"}</h1>
            <p className="muted">Доступ на {data?.plan.period || "выбранный период"}</p>
            {data?.mode === "demo" && <span className="test-badge">Тестовый режим</span>}
          </header>

          <ul className="benefits">
            {(data?.plan.benefits || []).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>

          <div className="summary">
            <div>
              <span className="muted">Тариф</span>
              <strong>{data?.plan.title}</strong>
            </div>
            <div>
              <span className="muted">Срок</span>
              <strong>{data?.plan.period}</strong>
            </div>
          </div>

          <div className="total">
            <span>К оплате</span>
            <strong>{data?.amount.label}</strong>
          </div>

          <label className="consent">
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
            />
            <span>
              Я принимаю{" "}
              <a href={data?.legal.terms_url || "/legal/terms"} target="_blank" rel="noopener noreferrer">
                условия подписки
              </a>{" "}
              и ознакомлен с{" "}
              <a href={data?.legal.privacy_url || "/legal/privacy"} target="_blank" rel="noopener noreferrer">
                политикой конфиденциальности
              </a>
              .
            </span>
          </label>

          {error && <p className="pay-error">{error}</p>}

          <button
            type="button"
            className="button button-primary pay-button"
            disabled={!consent || paying}
            onClick={() => void pay()}
          >
            {paying ? <Spinner /> : `Оплатить ${data?.amount.label || ""}`}
          </button>
          <p className="pay-hint">Банковская карта или СБП</p>

          {!data?.card_available && !data?.stars_available && (
            <p className="pay-error">Оплата временно недоступна. Попробуйте позже.</p>
          )}
        </section>
        {footer}
      </div>
    </main>
  );
}
