import { test, expect } from "@playwright/test";
import { loginViaUi } from "./helpers";

test("staff invite can be accepted from the login screen", async ({ page }) => {
  const suffix = Date.now().toString().slice(-7);
  const login = `e2e_inv_${suffix}`;
  const password = `Invite-${suffix}-Pass!`;

  await loginViaUi(page);
  await page.getByRole("navigation", { name: "Разделы" }).getByRole("button", { name: "Сотрудники" }).click();
  await page.getByPlaceholder("Логин").fill(login);
  await page.getByRole("button", { name: "Создать приглашение" }).click();
  const tokenBox = page.locator(".secret-box");
  await expect(tokenBox).toBeVisible();
  const token = (await tokenBox.innerText()).trim();
  expect(token.length).toBeGreaterThan(10);

  await page.getByRole("button", { name: "Выйти" }).click();
  await page.getByRole("button", { name: "Принять приглашение" }).click();
  await page.getByPlaceholder("Код приглашения").fill(token);
  await page.getByPlaceholder("Новый пароль (от 10 символов)").fill(password);
  await page.getByPlaceholder("Повторите пароль").fill(password);
  await page.getByRole("button", { name: "Создать аккаунт" }).click();
  await expect(page.getByText("Аккаунт создан", { exact: false })).toBeVisible();

  // The newly created account can sign in with its own password.
  await page.getByPlaceholder("Логин").fill(login);
  await page.getByPlaceholder("Пароль").fill(password);
  const response = page.waitForResponse(
    (r) => r.url().includes("/api/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Войти" }).click();
  expect((await response).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Обзор" })).toBeVisible({ timeout: 20_000 });
});
