import { test, expect, type Page } from "@playwright/test";

const TOKEN = "test-token";
const URL = `/checkout/${TOKEN}`;

function checkoutPayload(overrides: Record<string, unknown> = {}) {
  return {
    order_number: "PAY-000001",
    status: "created",
    plan: {
      code: "startup_pro",
      title: "Startup PRO",
      audience: "Для основателей стартапов",
      benefits: [
        "До 3 стартапов вместо 1",
        "До 3 фотографий в карточке",
        "Продвижение и аналитика (появятся позже)",
      ],
      period: "30 дней",
      hours: 720,
    },
    amount: { minor: 79900, currency: "RUB", label: "799 ₽" },
    mode: "provider_test",
    is_test: true,
    consent: { required: true, version: "payments-test", accepted: false },
    legal: {
      terms_url: "/legal/terms",
      privacy_url: "/legal/privacy",
      seller: null,
      refund_note: "Возврат рассматривается поддержкой RAUNDO через /support.",
    },
    card_available: true,
    stars_available: false,
    expires_at: new Date(Date.now() + 3600_000).toISOString(),
    paid_until: null,
    ...overrides,
  };
}

async function mockCheckout(page: Page, payload: Record<string, unknown>, payResult?: Record<string, unknown>) {
  await page.route(`**/api/public/checkout/${TOKEN}/pay`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payResult ?? { status: "demo", paid: false, message: "Тестовый режим" }),
    });
  });
  await page.route(`**/api/public/checkout/${TOKEN}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
  await page.route(`**/api/public/checkout/${TOKEN}/status`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}

test("checkout looks right on phone and desktop widths", async ({ page }) => {
  await mockCheckout(page, checkoutPayload());
  for (const width of [320, 360, 375, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width >= 768 ? 900 : 780 });
    await page.goto(`http://127.0.0.1:8091${URL}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "Startup PRO" })).toBeVisible();
    await expect(page.getByText("799 ₽").first()).toBeVisible();
    await expect(page.getByRole("button", { name: /Оплатить 799/ })).toBeDisabled();
    const noOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    );
    expect(noOverflow, `horizontal overflow at ${width}px`).toBe(true);
    await page.getByRole("checkbox").check();
    await expect(page.getByRole("button", { name: /Оплатить 799/ })).toBeEnabled();
  }
});

test("consent gates the payment button and demo never claims success", async ({ page }) => {
  await mockCheckout(page, checkoutPayload({ mode: "demo" }), {
    status: "demo",
    paid: false,
    message: "Тестовый режим RAUNDO: реальная оплата не выполняется, подписка не активируется.",
  });
  await page.goto(`http://127.0.0.1:8091${URL}`);
  const pay = page.getByRole("button", { name: /Оплатить 799/ });
  await expect(pay).toBeDisabled();
  await page.getByRole("checkbox").check();
  await expect(pay).toBeEnabled();
  await pay.click();
  await expect(page.getByRole("heading", { name: "Тестовый режим" })).toBeVisible();
  await expect(page.getByText("реальная оплата не выполняется", { exact: false })).toBeVisible();
  await expect(page.getByText("Оплата прошла")).toHaveCount(0);
});

test("pending, success, failed and expired states are honest", async ({ page }) => {
  await mockCheckout(page, checkoutPayload({ status: "pending" }));
  await page.goto(`http://127.0.0.1:8091${URL}`);
  await expect(page.getByRole("heading", { name: "Проверяем оплату" })).toBeVisible();

  await page.unrouteAll();
  await mockCheckout(
    page,
    checkoutPayload({ status: "paid", paid_until: "2026-12-03T12:00:00+00:00" }),
  );
  await page.goto(`http://127.0.0.1:8091${URL}`);
  await expect(page.getByRole("heading", { name: "Оплата прошла" })).toBeVisible();
  await expect(page.getByText("Startup PRO", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("03 декабря 2026", { exact: false })).toBeVisible();

  await page.unrouteAll();
  await mockCheckout(page, checkoutPayload({ status: "canceled" }));
  await page.goto(`http://127.0.0.1:8091${URL}`);
  await expect(page.getByRole("heading", { name: "Оплата не завершена" })).toBeVisible();

  await page.unrouteAll();
  await page.route(`**/api/public/checkout/${TOKEN}`, (route) =>
    route.fulfill({ status: 410, contentType: "application/json", body: "{}" }),
  );
  await page.goto(`http://127.0.0.1:8091${URL}`);
  await expect(page.getByRole("heading", { name: "Ссылка больше не действует" })).toBeVisible();
});

test("checkout screenshots", async ({ page }) => {
  await mockCheckout(page, checkoutPayload());
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`http://127.0.0.1:8091${URL}`);
  await page.screenshot({ path: "screenshots/checkout-desktop.png", fullPage: true });

  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`http://127.0.0.1:8091${URL}`);
  await page.getByRole("checkbox").check();
  await page.screenshot({ path: "screenshots/checkout-375.png", fullPage: true });

  await page.unrouteAll();
  await mockCheckout(
    page,
    checkoutPayload({ status: "paid", paid_until: "2026-12-03T12:00:00+00:00" }),
  );
  await page.goto(`http://127.0.0.1:8091${URL}`);
  await page.screenshot({ path: "screenshots/checkout-success-375.png", fullPage: true });
});
