import { defineConfig, devices } from "@playwright/test";
import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const credPath = resolve(__dirname, ".e2e-credentials.json");
const creds = existsSync(credPath)
  ? JSON.parse(readFileSync(credPath, "utf-8")) as { login: string; password: string }
  : { login: process.env.E2E_ADMIN_LOGIN || "owner", password: process.env.E2E_ADMIN_PASSWORD || "" };

const DEFAULT_DEV_DB =
  "postgresql+asyncpg://raundo:raundo_local_only@127.0.0.1:55432/raundo_admin_dev";
const databaseUrl = process.env.DATABASE_URL || DEFAULT_DEV_DB;

function dbNameOf(url: string): string {
  const text = url.replace(/^postgresql\+\w+:\/\//, "postgresql://");
  const parsed = new URL(text);
  return parsed.pathname.replace(/^\//, "").split("/")[0];
}

// Hard fail closed: E2E must never attach to the live bot DB.
const e2eDbName = dbNameOf(databaseUrl);
if (e2eDbName === "raundo" || e2eDbName !== "raundo_admin_dev") {
  throw new Error(
    `E2E refuses database '${e2eDbName}'. Only raundo_admin_dev is allowed; ` +
      "the live 'raundo' database is forbidden.",
  );
}
// Global setup and seed scripts must inherit the validated URL.
process.env.DATABASE_URL = databaseUrl;

const E2E_API_PORT = 8091;
const E2E_WEB_PORT = 3100;
const python = process.env.PYTHON_EXECUTABLE || (process.platform === "win32" ? ".venv\\Scripts\\python.exe" : "python");

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  expect: { timeout: 10_000 },
  use: {
    baseURL: `http://127.0.0.1:${E2E_WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  // Isolated pair: API 8091 + web 3100. Never reuse a live/dev server so a
  // running production API on 8090 can coexist without being touched.
  webServer: [
    {
      command: `${python} scripts/run_admin_test_api.py`,
      cwd: resolve(__dirname, ".."),
      url: `http://127.0.0.1:${E2E_API_PORT}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        DATABASE_URL: databaseUrl,
        PYTHONPATH: "",
        RAUNDO_E2E_API_PORT: String(E2E_API_PORT),
      },
    },
    {
      command: `npm run dev -- --port ${E2E_WEB_PORT}`,
      cwd: __dirname,
      url: `http://127.0.0.1:${E2E_WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { VITE_API_TARGET: `http://127.0.0.1:${E2E_API_PORT}` },
    },
  ],
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
});

export { creds };
