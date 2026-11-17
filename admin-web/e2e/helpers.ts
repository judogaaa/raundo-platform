import { expect, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

export function e2eCredentials(): { login: string; password: string } {
  const path = resolve(__dirname, "../.e2e-credentials.json");
  if (!existsSync(path)) {
    throw new Error("Нет .e2e-credentials.json. Запустите scripts/prep_e2e_admin.py");
  }
  return JSON.parse(readFileSync(path, "utf-8")) as {
    login: string;
    password: string;
    supportLogin: string;
    supportPassword: string;
    mfaLogin: string;
    mfaPassword: string;
  };
}

export async function loginViaUi(page: Page) {
  const { login, password } = e2eCredentials();
  await loginWith(page, login, password);
}

export async function loginWith(page: Page, login: string, password: string) {
  await page.context().clearCookies();
  // Dangerous admin actions ask for confirmation; E2E accepts them explicitly.
  page.on("dialog", (dialog) => {
    void dialog.accept();
  });
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const loginField = page.getByPlaceholder("Логин");
  try {
    // Vite compiles the bundle on first request; give the cold start time.
    await loginField.waitFor({ state: "visible", timeout: 25_000 });
  } catch {
    await page.reload({ waitUntil: "domcontentloaded" });
    await loginField.waitFor({ state: "visible", timeout: 30_000 });
  }
  await loginField.fill(login);
  await page.getByPlaceholder("Пароль").fill(password);
  const loginResponse = page.waitForResponse(
    (r) => r.url().includes("/api/auth/login") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Войти" }).click();
  const res = await loginResponse;
  expect(res.status(), "login HTTP status").toBe(200);
  await expect(page.getByRole("heading", { name: "Обзор" })).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(".card-label", { hasText: "Пользователи" })).toBeVisible({ timeout: 20_000 });
}

export async function openSection(page: Page, name: string) {
  const menu = page.locator(".mobile-menu");
  if (await menu.isVisible()) {
    await menu.click();
  }
  await page.getByRole("navigation", { name: "Разделы" }).getByRole("button", { name }).click();
}
