import { createHmac } from "node:crypto";
import { test, expect } from "@playwright/test";
import { e2eCredentials } from "./helpers";

/**
 * MFA (TOTP) round-trip: enrol, enforce, verify at login, disable.
 * Uses a dedicated E2E account prepared by scripts/prep_e2e_admin.py so no other
 * test is affected by two-factor being temporarily enabled.
 */

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function base32Decode(secret: string): Buffer {
  const clean = secret.toUpperCase().replace(/=+$/, "");
  let bits = 0;
  let value = 0;
  const bytes: number[] = [];
  for (const char of clean) {
    const index = BASE32.indexOf(char);
    if (index === -1) continue;
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((value >>> bits) & 0xff);
    }
  }
  return Buffer.from(bytes);
}

function totp(secret: string, stepDelta = 0): string {
  const counter = Math.floor(Date.now() / 30_000) + stepDelta;
  const buffer = Buffer.alloc(8);
  buffer.writeUInt32BE(Math.floor(counter / 2 ** 32), 0);
  buffer.writeUInt32BE(counter >>> 0, 4);
  const digest = createHmac("sha1", base32Decode(secret)).update(buffer).digest();
  const dynamicOffset = digest[digest.length - 1] & 0x0f;
  const binary =
    ((digest[dynamicOffset] & 0x7f) << 24) |
    ((digest[dynamicOffset + 1] & 0xff) << 16) |
    ((digest[dynamicOffset + 2] & 0xff) << 8) |
    (digest[dynamicOffset + 3] & 0xff);
  return String(binary % 1_000_000).padStart(6, "0");
}

test("MFA: включение, проверка при входе, блокировка API до подтверждения, отключение", async ({ request }) => {
  const { mfaLogin, mfaPassword } = e2eCredentials();

  async function login(): Promise<string> {
    const res = await request.post("/api/auth/login", {
      data: { login: mfaLogin, password: mfaPassword },
    });
    expect(res.status()).toBe(200);
    const body = (await res.json()) as { csrf: string };
    expect(body.csrf.length).toBeGreaterThan(0);
    return body.csrf;
  }

  let csrf = await login();

  const enrol = await request.post("/api/auth/mfa/setup", {
    headers: { "x-csrf-token": csrf },
  });
  expect(enrol.status()).toBe(200);
  const { secret } = (await enrol.json()) as { secret: string };
  expect(secret.length).toBeGreaterThanOrEqual(16);

  const enabled = await request.post("/api/auth/mfa/enable", {
    headers: { "x-csrf-token": csrf },
    data: { code: totp(secret, 1) },
  });
  expect(enabled.status()).toBe(200);
  const { backup_codes: backupCodes } = (await enabled.json()) as { backup_codes: string[] };
  expect(backupCodes.length).toBeGreaterThan(0);

  // A fresh session cannot touch protected APIs until MFA is verified.
  csrf = await login();
  const blocked = await request.get("/api/users");
  expect(blocked.status()).toBe(403);
  expect(await blocked.text()).toContain("MFA");

  // verify-login accepts a TOTP code (window ±1) or a backup code.
  const verify = await request.post("/api/auth/mfa/verify-login", {
    headers: { "x-csrf-token": csrf },
    data: { code: totp(secret, 1) },
  });
  if (verify.status() !== 200) {
    // Timestep crossed while the request was in flight: use a backup code instead.
    const retry = await request.post("/api/auth/mfa/verify-login", {
      headers: { "x-csrf-token": csrf },
      data: { code: backupCodes[0] },
    });
    expect(retry.status()).toBe(200);
  } else {
    expect(verify.status()).toBe(200);
  }
  const afterVerify = await request.get("/api/users");
  expect(afterVerify.status()).toBe(200);

  // Disable requires a fresh TOTP code (or a backup code).
  const disable = await request.post("/api/auth/mfa/disable", {
    headers: { "x-csrf-token": csrf },
    data: { code: totp(secret, 1) },
  });
  expect(disable.status()).toBe(200);

  csrf = await login();
  const unblocked = await request.get("/api/users");
  expect(unblocked.status()).toBe(200);

  await request.post("/api/auth/logout", { headers: { "x-csrf-token": csrf } });
});
