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
    await openSection(page, section);
    await expect(page.getByRole("heading", { name: section, exact: true })).toBeVisible();
    await expect(page.getByText("Загрузка…", { exact: true })).toHaveCount(0);
    await page.screenshot({ path: `screenshots/public-${file}.png`, fullPage: true });
  }
});
