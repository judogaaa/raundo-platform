import { test, expect } from "@playwright/test";
import { e2eCredentials, loginWith, openSection } from "./helpers";
import { totp } from "./totp";

test.beforeEach(async ({ page }) => {
  await page.context().clearCookies();
});

test("MFA через реальные кнопки: включение, вход с кодом, отключение, вход без кода", async ({
  page,
}) => {
  const { mfaLogin, mfaPassword } = e2eCredentials();

  async function passwordLogin() {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder("Логин").waitFor({ timeout: 20_000 });
    await page.getByPlaceholder("Логин").fill(mfaLogin);
    await page.getByPlaceholder("Пароль").fill(mfaPassword);
    await page.getByRole("button", { name: "Войти" }).click();
  }

  async function logout() {
    await page.getByRole("button", { name: "Выйти" }).click();
    await expect(page.getByRole("button", { name: "Войти" })).toBeVisible();
  }

  // 1. Enable MFA from Settings using the real buttons.
  await loginWith(page, mfaLogin, mfaPassword);
  await openSection(page, "Настройки");
  await expect(
    page.getByRole("heading", { name: "Двухфакторная аутентификация" }),
  ).toBeVisible();
  await expect(page.getByText("MFA выключена", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Включить MFA" }).click();
  await expect(page.getByAltText("QR-код для приложения-аутентификатора")).toBeVisible();
  const secret = (await page.getByTestId("mfa-secret").textContent())?.trim() ?? "";
  expect(secret.length).toBeGreaterThanOrEqual(16);

  await page.getByPlaceholder("Шестизначный код из приложения").fill("000000");
  await page.getByRole("button", { name: "Подтвердить и включить" }).click();
  await expect(page.locator(".error-text")).toContainText("Неверный код");

  await page.getByPlaceholder("Шестизначный код из приложения").fill(totp(secret, 1));
  await page.getByRole("button", { name: "Подтвердить и включить" }).click();
  await expect(page.getByText("MFA включена", { exact: true })).toBeVisible();
  await expect(page.locator(".codes-grid span")).toHaveCount(8);
  await expect(page.getByRole("button", { name: "Отключить MFA" })).toBeVisible();

  // 2. Reload keeps the enabled state from the server.
  await page.reload({ waitUntil: "domcontentloaded" });
  await openSection(page, "Настройки");
  await expect(page.getByText("MFA включена", { exact: true })).toBeVisible();

  // 3. Password-only login is blocked until the TOTP code is provided.
  await logout();
  await passwordLogin();
  await expect(page.getByRole("heading", { name: "Подтверждение входа" })).toBeVisible();
  const blocked = await page.request.get("/api/users");
  expect(blocked.status()).toBe(403);
  await page.getByPlaceholder("Код").fill("000000");
  await page.getByRole("button", { name: "Продолжить" }).click();
  await expect(page.locator(".error-text")).toContainText("Неверный код");
  await page.getByPlaceholder("Код").fill(totp(secret, 1));
  await page.getByRole("button", { name: "Продолжить" }).click();
  await expect(page.getByRole("heading", { name: "Обзор" })).toBeVisible();
  expect((await page.request.get("/api/users")).status()).toBe(200);

  // 4. Disable MFA through the confirmation dialog and a valid code.
  await openSection(page, "Настройки");
  await page.getByRole("button", { name: "Отключить MFA" }).click();
  const dialog = page.getByRole("dialog", {
    name: "Отключение двухфакторной аутентификации",
  });
  await expect(dialog).toBeVisible();
  await dialog.getByPlaceholder("Код из приложения или резервный код").fill("000000");
  await dialog.getByRole("button", { name: "Подтвердить отключение" }).click();
  await expect(dialog.locator(".error-text")).toContainText("Неверный код");
  await dialog.getByPlaceholder("Код из приложения или резервный код").fill(totp(secret, 1));
  await dialog.getByRole("button", { name: "Подтвердить отключение" }).click();
  await expect(page.locator(".ok-text")).toContainText("MFA отключена");
  await expect(page.getByText("MFA выключена", { exact: true })).toBeVisible();

  // 5. After disabling, password-only login opens the panel again.
  await logout();
  await passwordLogin();
  await expect(page.getByRole("heading", { name: "Обзор" })).toBeVisible();
  expect((await page.request.get("/api/users")).status()).toBe(200);
});
