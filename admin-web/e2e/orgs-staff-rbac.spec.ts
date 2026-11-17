import { test, expect } from "@playwright/test";
import { e2eFixtures } from "./fixtures";
import { e2eCredentials, loginViaUi, openSection } from "./helpers";

test.beforeEach(async ({ page }) => {
  await page.context().clearCookies();
});

test("организации: очередь, подтверждение и мандат представителя", async ({ page }) => {
  const fixtures = e2eFixtures();
  await loginViaUi(page);
  await openSection(page, "Организации");
  await expect(page.getByRole("heading", { name: "Организации" })).toBeVisible();

  const row = page.getByRole("row").filter({ hasText: fixtures.organizationName });
  await expect(row).toBeVisible();

  await row.getByRole("cell", { name: fixtures.organizationName }).click();
  await expect(page.getByRole("heading", { name: fixtures.organizationName })).toBeVisible();

  // Membership is present but cannot be verified until the organization itself is confirmed.
  await expect(page.locator(".panel", { hasText: "Представители" })).toContainText(
    "Сначала подтвердите организацию",
  );

  const approveOrg = page.waitForResponse(
    (r) => r.url().includes("/organizations/") && r.url().includes("/review") && r.status() === 200,
  );
  await page.getByRole("button", { name: "Подтвердить организацию" }).click();
  await approveOrg;

  // After the organization is confirmed, the representative can be verified with a mandate.
  const approveMembership = page.waitForResponse(
    (r) => r.url().includes("/memberships/") && r.url().includes("/review") && r.status() === 200,
  );
  await page.getByRole("button", { name: "Подтвердить с мандатом" }).click();
  const membership = await (await approveMembership).json();
  expect(membership.status).toBe("approved");
  expect(membership.mandate).toBe(true);
});

test("сотрудники: аккаунты, роли и приглашение с отзывом", async ({ page }) => {
  const { login, supportLogin } = e2eCredentials();
  await loginViaUi(page);
  await openSection(page, "Сотрудники");
  await expect(page.getByRole("heading", { name: "Аккаунты" })).toBeVisible();

  // The owner can see both admin accounts, none is currently disabled.
  await expect(page.getByRole("cell", { name: login })).toBeVisible();
  await expect(page.getByRole("cell", { name: supportLogin })).toBeVisible();

  // Owner-only hidden actions: the owner row must not offer role buttons.
  const ownerRow = page.getByRole("row").filter({ hasText: login });
  await expect(ownerRow.getByRole("button")).toHaveCount(0);

  // Invite flow: create a token for a unique login, then revoke it so the run stays idempotent.
  const inviteLogin = `e2e_invite_${Date.now().toString(36)}`;
  await page.getByPlaceholder("Логин").fill(inviteLogin);
  await page.getByRole("button", { name: "Создать приглашение" }).click();
  const tokenBox = page.locator(".secret-box");
  await expect(tokenBox).toBeVisible();
  const inviteRow = page.locator(".row", { hasText: inviteLogin });
  await expect(inviteRow.getByRole("button", { name: "Отозвать" })).toBeVisible();
  await inviteRow.getByRole("button", { name: "Отозвать" }).click();
  await expect(page.locator(".ok-text")).toContainText("Приглашение отозвано");
});

test("RBAC: support видит только разрешённые разделы и API", async ({ page }) => {
  const { supportLogin, supportPassword } = e2eCredentials();
  await page.context().clearCookies();
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder("Логин").fill(supportLogin);
  await page.getByPlaceholder("Пароль").fill(supportPassword);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("heading", { name: "Обзор" })).toBeVisible();

  // Owner/moderator sections must be unavailable in the navigation.
  for (const name of ["Модерация", "Premium", "Сотрудники", "Журнал действий"]) {
    await expect(
      page.getByRole("navigation", { name: "Разделы" }).getByRole("button", { name }),
    ).toHaveCount(0);
  }

  // Server-side enforcement: restricted APIs reject the support session, allowed ones work.
  const staff = await page.request.get("/api/staff");
  expect(staff.status()).toBe(403);
  const moderation = await page.request.get("/api/moderation/queue");
  expect(moderation.status()).toBe(403);
  const audit = await page.request.get("/api/audit");
  expect(audit.status()).toBe(403);
  const premium = await page.request.get("/api/premium");
  expect(premium.status()).toBe(403);
  const users = await page.request.get("/api/users");
  expect(users.status()).toBe(200);
  const organizations = await page.request.get("/api/organizations");
  expect(organizations.status()).toBe(200);
  const complaints = await page.request.get("/api/complaints");
  expect(complaints.status()).toBe(200);
  const analytics = await page.request.get("/api/analytics");
  expect(analytics.status()).toBe(200);
});
