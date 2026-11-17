import { test, expect } from "@playwright/test";
import { loginViaUi } from "./helpers";
import { e2eFixtures } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.context().clearCookies();
});

test("investor moderation approve with cheque and strategy", async ({ page }) => {
  const { investorDisplayName } = e2eFixtures();
  await loginViaUi(page);
  await page.getByRole("button", { name: "Модерация" }).click();
  await page.getByRole("cell", { name: investorDisplayName }).click();
  await expect(page.getByRole("heading", { name: investorDisplayName })).toBeVisible();
  await expect(page.locator(".cheque-value")).toContainText("Обсуждаю индивидуально");
  await expect(page.locator(".review-block").getByText("s".repeat(100))).toBeVisible();
  await expect(page.locator(".media-img").first()).toBeVisible();
  const approve = page.waitForResponse(
    (r) => r.url().includes("/investors/") && r.url().includes("/approve") && r.status() === 200,
  );
  await page.getByRole("button", { name: "Одобрить" }).click();
  await approve;
  await page.getByRole("button", { name: "Модерация" }).click();
  await expect(page.getByRole("cell", { name: investorDisplayName })).toHaveCount(0);
});
