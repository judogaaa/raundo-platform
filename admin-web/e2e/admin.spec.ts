import { test, expect } from "@playwright/test";
import { e2eFixtures } from "./fixtures";
import { loginViaUi } from "./helpers";

test.beforeEach(async ({ page }) => {
  await page.context().clearCookies();
});

test("login and overview", async ({ page }) => {
  await loginViaUi(page);
  await expect(page.locator(".card-label", { hasText: "Пользователи" })).toBeVisible();
});

test("global search", async ({ page }) => {
  const fixtures = e2eFixtures();
  await loginViaUi(page);
  await page.locator("#global-search").fill(fixtures.startupTitle);
  const results = page.getByRole("listbox");
  await expect(results).toBeVisible();
  await expect(results).toContainText(fixtures.startupTitle);
  await results.getByRole("button", { name: new RegExp(fixtures.startupTitle.slice(0, 12)) }).click();
  await expect(page.getByRole("heading", { name: "Модерация" })).toBeVisible();
});

test("moderation queue", async ({ page }) => {
  await loginViaUi(page);
  await page.getByRole("button", { name: "Модерация" }).click();
  await expect(page.getByRole("heading", { name: "Модерация" })).toBeVisible();
});

test("support inbox", async ({ page }) => {
  await loginViaUi(page);
  await page.getByRole("button", { name: "Поддержка" }).click();
  await expect(page.getByRole("heading", { name: "Поддержка" })).toBeVisible();
});

test("theme toggle", async ({ page }) => {
  await loginViaUi(page);
  await page.getByRole("button", { name: /тема/i }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("screenshot overview", async ({ page }) => {
  await loginViaUi(page);
  await page.screenshot({ path: "test-results/overview-1440.png", fullPage: true });
});
