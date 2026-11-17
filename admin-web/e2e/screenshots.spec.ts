import { test, expect } from "@playwright/test";
import { e2eFixtures } from "./fixtures";
import { loginViaUi, openSection } from "./helpers";

const sizes = [
  { name: "1440", width: 1440, height: 900 },
  { name: "1280", width: 1280, height: 800 },
  { name: "768", width: 768, height: 1024 },
  { name: "375", width: 375, height: 812 },
];

const themes = ["light", "dark"] as const;

for (const size of sizes) {
  for (const theme of themes) {
    test(`screenshot investor card ${size.name} ${theme}`, async ({ page }) => {
      const fixtures = e2eFixtures();
      await page.setViewportSize({ width: size.width, height: size.height });
      await page.addInitScript((value) => {
        localStorage.setItem("raundo-admin-theme", value);
      }, theme);
      await loginViaUi(page);
      await openSection(page, "Модерация");
      await expect(page.getByRole("heading", { name: "Модерация" })).toBeVisible();
      const detailLoaded = page.waitForResponse(
        (r) => r.url().includes("/moderation/investors/reviews/") && r.request().method() === "GET" && r.ok(),
      );
      await page.getByRole("cell", { name: fixtures.investorCardName }).click();
      await detailLoaded;
      await expect(page.getByRole("heading", { name: fixtures.investorCardName })).toBeVisible();
      await expect(page.locator(".cheque-value")).toBeVisible();
      await page.screenshot({
        path: `screenshots/investor-card-${size.name}-${theme}.png`,
        fullPage: true,
      });
    });
  }
}

for (const size of sizes) {
  for (const theme of themes) {
    test(`screenshot investor revision ${size.name} ${theme}`, async ({ page }) => {
      const fixtures = e2eFixtures();
      await page.setViewportSize({ width: size.width, height: size.height });
      await page.addInitScript((value) => {
        localStorage.setItem("raundo-admin-theme", value);
      }, theme);
      await loginViaUi(page);
      await openSection(page, "Модерация");
      const detailLoaded = page.waitForResponse(
        (r) => r.url().includes("/moderation/investors/reviews/") && r.request().method() === "GET" && r.ok(),
      );
      await page.getByRole("cell", { name: fixtures.investorRevisionName }).click();
      await detailLoaded;
      await expect(page.getByRole("heading", { name: fixtures.investorRevisionName })).toBeVisible();
      await expect(page.locator(".review-diff-row").first()).toBeVisible();
      await page.screenshot({
        path: `screenshots/investor-revision-${size.name}-${theme}.png`,
        fullPage: true,
      });
      const diffHeading = page.getByRole("heading", { name: "Что изменилось" });
      await diffHeading.scrollIntoViewIfNeeded();
      await page.locator(".review-diff-row").first().evaluate((el) => {
        el.scrollIntoView({ block: "center" });
      });
      await page.waitForTimeout(150);
      await page.screenshot({
        path: `screenshots/investor-revision-${size.name}-${theme}-diff.png`,
      });
      await expect(page.locator(".review-diff-cols").first()).toBeVisible();
    });
  }
}

for (const size of sizes) {
  for (const theme of themes) {
    test(`screenshot moderation card ${size.name} ${theme}`, async ({ page }) => {
      const fixtures = e2eFixtures();
      await page.setViewportSize({ width: size.width, height: size.height });
      await page.addInitScript((value) => {
        localStorage.setItem("raundo-admin-theme", value);
      }, theme);
      await loginViaUi(page);
      await openSection(page, "Модерация");
      await expect(page.getByRole("heading", { name: "Модерация" })).toBeVisible();
      const detailLoaded = page.waitForResponse(
        (r) => r.url().includes("/moderation/startups/reviews/") && r.request().method() === "GET" && r.ok(),
      );
      await page.getByRole("cell", { name: fixtures.moderationCardTitle }).click();
      await detailLoaded;
      await expect(page.getByRole("heading", { name: fixtures.moderationCardTitle })).toBeVisible();
      await expect(page.locator(".review-columns")).toBeVisible();
      await page.screenshot({
        path: `screenshots/moderation-card-${size.name}-${theme}.png`,
        fullPage: true,
      });
    });
  }
}

for (const size of sizes) {
  test(`screenshot overview ${size.name}`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await loginViaUi(page);
    await page.screenshot({ path: `screenshots/overview-${size.name}.png`, fullPage: true });
    await openSection(page, "Модерация");
    await expect(page.getByRole("heading", { name: "Модерация" })).toBeVisible();
    await page.screenshot({ path: `screenshots/moderation-${size.name}.png`, fullPage: true });
  });
}

for (const size of sizes) {
  for (const theme of themes) {
    test(`screenshot users and complaints ${size.name} ${theme}`, async ({ page }) => {
      const fixtures = e2eFixtures();
      await page.setViewportSize({ width: size.width, height: size.height });
      await page.addInitScript((value) => {
        localStorage.setItem("raundo-admin-theme", value);
      }, theme);
      await loginViaUi(page);

      await openSection(page, "Пользователи");
      await expect(page.getByRole("heading", { name: "Пользователи" })).toBeVisible();
      await expect(page.locator(".data-table tbody tr").first()).toBeVisible();
      await page.screenshot({
        path: `screenshots/users-${size.name}-${theme}.png`,
        fullPage: true,
      });

      if (size.name === "1440") {
        await page
          .getByLabel("Поиск пользователей")
          .fill(String(fixtures.complaintTargetTelegramId));
        await page
          .getByRole("row")
          .filter({ hasText: String(fixtures.complaintTargetTelegramId) })
          .getByRole("button", { name: "Открыть" })
          .click();
        await expect(page.getByRole("heading", { name: "Нарушитель E2E" })).toBeVisible();
        await page.screenshot({
          path: `screenshots/user-card-${size.name}-${theme}.png`,
          fullPage: true,
        });
      }

      await openSection(page, "Жалобы");
      await expect(page.getByRole("heading", { name: "Жалобы" })).toBeVisible();
      const complaintRow = page.getByRole("row").filter({ hasText: fixtures.complaintNumber });
      try {
        await expect(complaintRow).toBeVisible({ timeout: 15_000 });
      } catch {
        // Cold data load after seeding can be slower than the first paint.
        await page.reload({ waitUntil: "domcontentloaded" });
        await openSection(page, "Жалобы");
        await expect(complaintRow).toBeVisible({ timeout: 20_000 });
      }
      await page.screenshot({
        path: `screenshots/complaints-${size.name}-${theme}.png`,
        fullPage: true,
      });

      if (size.name === "1440") {
        await page
          .getByRole("row")
          .filter({ hasText: fixtures.complaintNumber })
          .getByRole("button", { name: "Открыть" })
          .click();
        await expect(page.getByRole("heading", { name: fixtures.complaintNumber })).toBeVisible();
        await page.screenshot({
          path: `screenshots/complaint-card-${size.name}-${theme}.png`,
          fullPage: true,
        });
      }
    });
  }
}
