import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

export function e2eFixtures() {
  const path = resolve(__dirname, "../.e2e-fixtures.json");
  if (!existsSync(path)) {
    throw new Error("Нет .e2e-fixtures.json. Запустите seed_e2e_fixtures.py");
  }
  return JSON.parse(readFileSync(path, "utf-8")) as {
    startupTitle: string;
    startupChangesTitle: string;
    investorDisplayName: string;
    ticketNumber: string;
    ticketClarifyNumber: string;
    ticketConfirmedNumber: string;
    complaintNumber: string;
    complaintTargetTelegramId: number;
    actionUserTelegramId: number;
    restrictedUserTelegramId: number;
    organizationName: string;
    moderationCardTitle: string;
    investorCardName: string;
    investorRevisionName: string;
  };
}
