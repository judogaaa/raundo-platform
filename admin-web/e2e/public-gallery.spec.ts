import { test, expect } from "@playwright/test";
import { loginViaUi, openSection } from "./helpers";

// Only the isolated E2E database and fictional seed accounts are used.
test("public interface gallery", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginViaUi(page);
  for (const [section, file] of [
    ["Каталог", "catalog"], ["Модерация", "moderation"],
    ["Поддержка", "support"], ["Premium", "premium"], ["Обзор", "overview"],
  ]) {
    const loaded = page.waitForResponse((response) =>
      response.url().includes(section === "Каталог" ? "/api/catalog?" : "/api/") && response.request().method() === "GET"
    );
    await openSection(page, section);
    expect((await loaded).ok()).toBeTruthy();
    await expect(page.getByRole("heading", { name: section, exact: true })).toBeVisible();
    await expect(page.getByText("Загрузка…", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Internal Server Error", { exact: true })).toHaveCount(0);
    await page.screenshot({ path: `screenshots/public-${file}.png`, fullPage: true });
  }
});
