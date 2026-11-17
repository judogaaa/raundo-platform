import { execFileSync, execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

function assertDevDatabase(url: string) {
  const text = url.replace(/^postgresql\+\w+:\/\//, "postgresql://");
  const parsed = new URL(text);
  const name = parsed.pathname.replace(/^\//, "").split("/")[0];
  if (name === "raundo") {
    throw new Error("E2E refuses live database 'raundo' during setup.");
  }
  if (name !== "raundo_admin_dev") {
    throw new Error(`E2E refuses database '${name}' during setup.`);
  }
}

export default function globalSetup() {
  if (!process.env.DATABASE_URL) {
    process.env.DATABASE_URL =
      "postgresql+asyncpg://raundo:raundo_local_only@127.0.0.1:55432/raundo_admin_dev";
  }
  assertDevDatabase(process.env.DATABASE_URL);

  const python = process.env.PYTHON_EXECUTABLE || (process.platform === "win32" ? resolve(root, ".venv/Scripts/python.exe") : "python");

  // Verify the real connection (SELECT current_database), not just the env string.
  const guard = execFileSync(
    python,
    ["-c", "from raundo.testing.e2e_guard import assert_e2e_db_url; import os; assert_e2e_db_url(os.environ['DATABASE_URL']); print('e2e db guard ok')"],
    { cwd: root, env: process.env, encoding: "utf-8" },
  );
  if (!guard.includes("e2e db guard ok")) {
    throw new Error(`E2E DB guard failed: ${guard}`);
  }

  const creds = resolve(root, "admin-web/.e2e-credentials.json");
  // Always reset E2E accounts (MFA enrollment is cleared for the dedicated
  // mfa account); passwords stay unchanged unless E2E_ADMIN_PASSWORD is set.
  execSync(`"${python}" scripts/prep_e2e_admin.py`, {
    cwd: root,
    stdio: "inherit",
    env: process.env,
  });
  execSync(`"${python}" scripts/seed_e2e_fixtures.py`, {
    cwd: root,
    env: process.env,
    stdio: "inherit",
  });
  if (!existsSync(resolve(root, "admin-web/.e2e-credentials.json"))) {
    throw new Error("E2E credentials missing after prep");
  }
}
