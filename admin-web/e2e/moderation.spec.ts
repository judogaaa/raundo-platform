import { test, expect } from "@playwright/test";
import { loginViaUi } from "./helpers";
import { e2eFixtures } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.context().clearCookies();
});

test("startup moderation approve with audit and outbox", async ({ page }) => {
  const { startupTitle } = e2eFixtures();
  await loginViaUi(page);
  await page.getByRole("button", { name: "Модерация" }).click();
  const detailLoaded = page.waitForResponse(
    (r) => r.url().includes("/moderation/startups/reviews/") && r.request().method() === "GET" && r.ok(),
  );
  await page.getByRole("cell", { name: startupTitle }).click();
  await detailLoaded;
  await expect(page.getByRole("heading", { name: startupTitle })).toBeVisible();
  await expect(page.locator(".media-img").first()).toBeVisible();
  const approve = page.waitForResponse(
    (r) => r.url().includes("/approve") && r.request().method() === "POST" && r.status() === 200,
  );
  await page.getByRole("button", { name: "Одобрить" }).click();
  await approve;
  await page.getByRole("button", { name: "Модерация" }).click();
  await expect(page.getByRole("cell", { name: startupTitle })).toHaveCount(0);
  const audit = await page.evaluate(async () => {
    const r = await fetch("/api/audit", { credentials: "include" });
    return r.json() as Promise<{ items: { action: string; resource_type: string }[] }>;
  });
  expect(audit.items.some((i) => i.action === "approve" && i.resource_type === "startup")).toBeTruthy();
  const outbox = await page.evaluate(async () => {
    const r = await fetch("/api/outbox", { credentials: "include" });
    return r.json() as Promise<{ items: { kind: string }[] }>;
  });
  expect(outbox.items.some((i) => i.kind === "startup.approved")).toBeTruthy();
});

test("startup request changes", async ({ page }) => {
  const { startupChangesTitle } = e2eFixtures();
  await loginViaUi(page);
  await page.getByRole("button", { name: "Модерация" }).click();
  const detailLoaded = page.waitForResponse(
    (r) => r.url().includes("/moderation/startups/reviews/") && r.request().method() === "GET" && r.ok(),
  );
  await page.getByRole("cell", { name: startupChangesTitle }).click();
  await detailLoaded;
  await expect(page.locator("textarea")).toBeVisible();
  await page.locator("textarea").fill("Уточните описание продукта подробнее.");
  const changes = page.waitForResponse(
    (r) => r.url().includes("/changes") && r.request().method() === "POST" && r.status() === 200,
  );
  await page.getByRole("button", { name: "Запросить изменения" }).click();
  await changes;
  await page.getByRole("button", { name: "Модерация" }).click();
  await expect(page.getByRole("cell", { name: startupChangesTitle })).toHaveCount(0);
});
