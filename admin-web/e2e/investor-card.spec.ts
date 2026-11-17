import { test, expect } from "@playwright/test";
import { e2eFixtures } from "./fixtures";
import { loginViaUi, openSection } from "./helpers";

test.beforeEach(async ({ page }) => {
  await page.context().clearCookies();
});

test("инвестор: стратегия, чек, предпочтения и независимый мандат", async ({ page }) => {
  const fixtures = e2eFixtures();
  await loginViaUi(page);
  await openSection(page, "Модерация");

  const loaded = page.waitForResponse(
    (r) => r.url().includes("/moderation/investors/reviews/") && r.request().method() === "GET" && r.ok(),
  );
  await page.getByRole("cell", { name: fixtures.investorCardName }).click();
  await loaded;

  await expect(page.getByRole("heading", { name: fixtures.investorCardName })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Инвестиционная стратегия" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Инвестиционный чек" })).toBeVisible();
  // Human-readable cheque, never minor units.
  await expect(page.locator(".cheque-value")).toContainText("млн");
  await expect(page.locator(".cheque-value")).not.toContainText("000000");
  // Long strategy is collapsed first and can be expanded.
  await expect(page.getByRole("button", { name: "Показать полностью" }).first()).toBeVisible();

  await expect(page.getByRole("heading", { name: "Инвестиционные предпочтения" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Организация" })).toBeVisible();
  await expect(page.locator(".mandate-warning")).toHaveText("Полномочия не подтверждены");
  await expect(page.getByText("Проверка организации")).toBeVisible();
  await expect(page.getByText("Принадлежность представителя")).toBeVisible();

  // First application: no artificial diff, just the honest note.
  await expect(page.getByText("Это первая заявка инвестора")).toBeVisible();
});

test("инвестор: повторная редакция показывает человекочитаемый diff", async ({ page }) => {
  const fixtures = e2eFixtures();
  await loginViaUi(page);
  await openSection(page, "Модерация");

  const loaded = page.waitForResponse(
    (r) => r.url().includes("/moderation/investors/reviews/") && r.request().method() === "GET" && r.ok(),
  );
  await page.getByRole("cell", { name: fixtures.investorRevisionName }).click();
  await loaded;

  await expect(page.getByRole("heading", { name: fixtures.investorRevisionName })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Что изменилось" })).toBeVisible();
  const firstRow = page.locator(".review-diff-row").first();
  await expect(firstRow).toBeVisible();
  // Field labels are human words, never raw backend keys.
  await expect(firstRow).not.toContainText("description");
  await expect(firstRow).not.toContainText("strategy");
  // Both sides of the change are rendered.
  await expect(page.getByText("Не было").first()).toBeVisible();
  await expect(page.locator(".review-diff-cols")).toHaveCount(
    await page.locator(".review-diff-row").count(),
  );
});
