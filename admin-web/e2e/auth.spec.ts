import { test, expect } from "@playwright/test";
import { loginViaUi, e2eCredentials } from "./helpers";

test.describe.configure({ mode: "serial" });

test("login session me dashboard logout x3", async ({ page }) => {
  for (let i = 0; i < 3; i += 1) {
    await loginViaUi(page);
    const body = await page.evaluate(async () => {
      const r = await fetch("/api/auth/me", { credentials: "include" });
      if (!r.ok) throw new Error(`me ${r.status}`);
      return r.json() as Promise<{ login: string; mfa_pending?: boolean }>;
    });
    expect(body.login).toBe(e2eCredentials().login);
    expect(body.mfa_pending).toBeFalsy();
    await page.getByRole("button", { name: "Настройки" }).click();
    await expect(page.getByRole("heading", { name: "Настройки" })).toBeVisible();
    await page.context().clearCookies();
    await page.goto("/");
    await expect(page.getByPlaceholder("Логин")).toBeVisible();
  }
});
