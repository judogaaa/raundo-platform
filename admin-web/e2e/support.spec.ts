import { test, expect } from "@playwright/test";
import { loginViaUi } from "./helpers";
import { e2eFixtures } from "./fixtures";

async function openSupport(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Поддержка" }).click();
}

async function openTicket(page: import("@playwright/test").Page, number: string) {
  await openSupport(page);
  await page.getByRole("button", { name: number }).click();
  await expect(page.locator(".split-right h2")).toHaveText(number);
}

function statusBadge(page: import("@playwright/test").Page) {
  return page.locator(".split-right .row .badge").first();
}

async function openTicketsFromApi(page: import("@playwright/test").Page): Promise<number> {
  const res = await page.request.get("/api/overview");
  expect(res.status()).toBe(200);
  const body = (await res.json()) as { open_tickets: number };
  return body.open_tickets;
}

test("support: staff answer waits for the user's confirmation", async ({ page }) => {
  const { ticketNumber } = e2eFixtures();
  await loginViaUi(page);
  const before = await openTicketsFromApi(page);

  await openTicket(page, ticketNumber);
  await expect(page.getByText("Тестовое сообщение E2E")).toBeVisible();
  await page.locator("textarea").fill("Окончательный ответ E2E");
  const countersRefresh = page.waitForResponse(
    (r) => r.url().includes("/api/overview") && r.request().method() === "GET",
    { timeout: 15_000 },
  );
  await page.getByRole("button", { name: "Отправить ответ" }).click();
  await countersRefresh;

  await expect(
    page.getByText(/Ждём, пока пользователь подтвердит решение|Ждём подтверждения пользователя/),
  ).toBeVisible();
  await expect(statusBadge(page)).toHaveText("Ожидает подтверждения");
  await expect(page.getByText("Окончательный ответ E2E")).toBeVisible();
  // The ticket is still active until the user confirms.
  expect(await openTicketsFromApi(page)).toBe(before);

  await page.reload();
  await openSupport(page);
  await page.getByRole("button", { name: "Ждут подтверждения" }).click();
  await page.getByRole("button", { name: ticketNumber }).click();
  await expect(statusBadge(page)).toHaveText("Ожидает подтверждения");
  await expect(page.getByText("Окончательный ответ E2E")).toBeVisible();
  await expect(
    page.locator(".thread .bubble.staff").filter({ hasText: "Окончательный ответ E2E" }),
  ).toBeVisible();
  const delivery = page
    .locator(".thread .bubble.staff .badge")
    .filter({ hasText: /Доставлено|Отправляется|В очереди|Повтор/ })
    .first();
  await expect(delivery).toBeVisible();
});

test("support: clarification is a separate state without confirmation buttons", async ({ page }) => {
  const { ticketClarifyNumber } = e2eFixtures();
  await loginViaUi(page);
  await openTicket(page, ticketClarifyNumber);
  await page.locator("textarea").fill("Уточните, пожалуйста, стадию проекта");
  await page.getByRole("button", { name: "Запросить уточнение" }).click();

  await expect(page.getByText("Уточнение отправлено", { exact: false })).toBeVisible();
  await expect(statusBadge(page)).toHaveText("Ожидает уточнения");
});

test("support: internal note is stored without delivery and without status change", async ({
  page,
}) => {
  const { ticketClarifyNumber } = e2eFixtures();
  await loginViaUi(page);
  await openTicket(page, ticketClarifyNumber);
  await page.getByLabel("Внутренняя заметка").check();
  await page.locator("textarea").fill("Внутренняя заметка E2E");
  await page.getByRole("button", { name: "Сохранить заметку" }).click();

  await expect(page.getByText("Заметка сохранена", { exact: false })).toBeVisible();
  await expect(page.locator(".bubble.internal").filter({ hasText: "Внутренняя заметка E2E" })).toBeVisible();
  await expect(statusBadge(page)).not.toHaveText("Решено");
  await expect(page.locator(".bubble.internal .badge")).toHaveCount(0);
});

test("support: user-confirmed ticket shows resolution author", async ({ page }) => {
  const { ticketConfirmedNumber } = e2eFixtures();
  await loginViaUi(page);
  await openSupport(page);
  await page.getByRole("button", { name: "Решённые" }).click();
  await page.getByRole("button", { name: ticketConfirmedNumber }).click();
  await expect(statusBadge(page)).toHaveText("Решено");
  await expect(page.getByText("Подтверждено пользователем", { exact: false })).toBeVisible();
  await expect(page.getByText("Пользователь подтвердил, что вопрос решён")).toBeVisible();
});
