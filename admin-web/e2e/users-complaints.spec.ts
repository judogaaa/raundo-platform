import { test, expect } from "@playwright/test";
import { e2eFixtures } from "./fixtures";
import { loginViaUi, openSection } from "./helpers";

test("пользователи: поиск, карточка 360 и реальное ограничение доступа", async ({ page }) => {
  const fixtures = e2eFixtures();
  await loginViaUi(page);
  await openSection(page, "Пользователи");
  await expect(page.getByRole("heading", { name: "Пользователи" })).toBeVisible();

  const search = page.getByLabel("Поиск пользователей");
  await search.fill(String(fixtures.actionUserTelegramId));
  // The search is debounced: wait for the filtered list before picking a row, otherwise the
  // click lands on whichever user happened to be first in the unfiltered table.
  const actionRow = page
    .getByRole("row")
    .filter({ hasText: String(fixtures.actionUserTelegramId) });
  await expect(page.locator(".data-table tbody tr")).toHaveCount(1);
  await expect(actionRow).toBeVisible();
  await expect(page.getByRole("cell", { name: "@e2e_action_user" })).toBeVisible();

  await actionRow.getByRole("button", { name: "Открыть" }).click();
  await expect(page.getByRole("heading", { name: "Действие E2E" })).toBeVisible();
  for (const sectionName of [
    "Стартапы",
    "Инвесторские профили",
    "Организации",
    "Premium-подписки",
    "История модерации",
    "Обращения в поддержку",
    "Жалобы",
    "История административных действий",
  ]) {
    await expect(page.getByRole("heading", { name: sectionName, exact: true })).toBeVisible();
  }

  // Access restriction reaches PostgreSQL and is announced through the outbox.
  await page.getByRole("button", { name: "Ограничить доступ" }).click();
  await page.getByLabel("Причина ограничения").fill("E2E: подтверждённое нарушение правил");
  await page.getByLabel("Подтверждаю ограничение и уведомление пользователя").check();
  await page.getByRole("button", { name: "Применить ограничение" }).click();
  await expect(page.locator(".ok-text")).toContainText("Доступ ограничен");
  await expect(page.locator(".callout.danger")).toContainText("E2E: подтверждённое нарушение");

  // The UI only offers the remaining legal action after the restriction is applied.
  await page.getByRole("button", { name: "Снять ограничение" }).click();
  await page.getByLabel("Причина снятия ограничения").fill("E2E: ограничение снято после проверки");
  await page.getByLabel("Подтверждаю снятие ограничения").check();
  await page.getByRole("button", { name: "Снять и уведомить" }).click();
  await expect(page.locator(".ok-text")).toContainText("Ограничение снято");
  await expect(page.locator(".callout.danger")).toHaveCount(0);
});

test("пользователи: поиск по username и название стартапа", async ({ page }) => {
  const fixtures = e2eFixtures();
  await loginViaUi(page);
  await openSection(page, "Пользователи");

  const search = page.getByLabel("Поиск пользователей");
  await search.fill("e2e_investor_user");
  await expect(page.getByRole("cell", { name: "@e2e_investor_user" })).toBeVisible();
  await expect(page.getByRole("cell", { name: String(fixtures.actionUserTelegramId) })).toHaveCount(0);

  await search.fill(fixtures.startupChangesTitle);
  await expect(page.getByRole("cell", { name: "@e2e_founder" })).toBeVisible();

  await search.fill("");
  await page.getByLabel("Тип профиля").selectOption("investor");
  await expect(page.getByRole("cell", { name: "@e2e_investor_user" })).toBeVisible();
});

test("жалобы: фильтры, карточка, решение и сохранённый результат", async ({ page }) => {
  const fixtures = e2eFixtures();
  await loginViaUi(page);
  await openSection(page, "Жалобы");
  await expect(page.getByRole("heading", { name: "Жалобы" })).toBeVisible();

  await page.getByRole("button", { name: "Новая", exact: true }).click();
  const row = page.getByRole("row").filter({ hasText: fixtures.complaintNumber });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Открыть" }).click();

  await expect(page.getByRole("heading", { name: fixtures.complaintNumber })).toBeVisible();
  await expect(page.locator(".reason-text")).toContainText("оскорбления и угрозы");
  await expect(page.getByText("Нарушитель E2E", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("Фотография")).toBeVisible();
  await expect(page.getByText("E2E Founder", { exact: false }).first()).toBeVisible();

  // Accept into work (no comment required at this stage).
  await page.getByRole("button", { name: "Принять в работу" }).click();
  await page.getByRole("button", { name: "Сохранить решение" }).click();
  await expect(page.locator(".ok-text")).toContainText("Решение сохранено");

  // Resolve with a reason and explicit confirmation.
  await page.getByRole("button", { name: "Рассмотреть" }).click();
  await page
    .getByLabel("Комментарий к решению")
    .fill("E2E: нарушение подтверждено, пользователь уведомлён");
  await page
    .getByLabel("Подтверждаю решение: оно сохранится в журнале и будет отправлено заявителю")
    .check();
  await page.getByRole("button", { name: "Сохранить решение" }).click();
  await expect(page.locator(".ok-text")).toContainText("Решение сохранено");
  await expect(page.locator(".callout")).toContainText("E2E: нарушение подтверждено");
  await expect(page.getByRole("heading", { name: "История обработки" })).toBeVisible();

  // The state machine no longer offers the same decision: no duplicate submit.
  await expect(page.getByRole("button", { name: "Рассмотреть" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Вернуть в работу" })).toBeVisible();
});

test("навигация: из жалобы к пользователю и назад с сохранёнными фильтрами", async ({ page }) => {
  const fixtures = e2eFixtures();
  await loginViaUi(page);
  await openSection(page, "Жалобы");

  const search = page.getByLabel("Поиск жалоб");
  await search.fill(fixtures.complaintNumber);
  await page.getByRole("row").filter({ hasText: fixtures.complaintNumber }).getByRole("button", {
    name: "Открыть",
  }).click();

  await page.getByTestId("complaint-reporter").getByRole("button", { name: "Открыть профиль" }).click();
  await expect(page.getByRole("heading", { name: "Пользователи" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "E2E Founder", exact: true })).toBeVisible();

  await openSection(page, "Жалобы");
  await expect(page.getByLabel("Поиск жалоб")).toHaveValue(fixtures.complaintNumber);
});

test("жалобы: переход к профилю объекта жалобы", async ({ page }) => {
  const fixtures = e2eFixtures();
  await loginViaUi(page);
  await openSection(page, "Жалобы");
  await page.getByRole("button", { name: "Все", exact: true }).click();
  await page.getByRole("row").filter({ hasText: fixtures.complaintNumber }).getByRole("button", {
    name: "Открыть",
  }).click();
  await page.getByTestId("complaint-target").getByRole("button", { name: "Открыть профиль" }).click();
  await expect(page.getByRole("heading", { name: "Нарушитель E2E" })).toBeVisible();
});
