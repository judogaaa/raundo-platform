# RAUNDO — безопасность, конфиденциальность, модерация и правовые границы

**This is not a legal opinion.** Jurisdiction, hosting region, data operator and applicable Russian requirements must be reviewed by qualified counsel before launch; especially personal data law and advertising/payment rules.

## 1. Abuse models

- Fake investor identity/wealth, fake startup founder, stolen media, false website claims;
- impersonation by Telegram mutable username; client-forged callback to reveal hidden contact or grant admin;
- repeated updates/vote farming/paid rank manipulation, cold spam requests;
- support ticket message leakage or cross-user access, harmful attachments/URLs;
- fake payment invoices/callbacks, stale subscription entitlements;
- moderator abusing access or reviewing own startup/portfolio without conflict logging;
- unsolicited investor mass-DMs, reporting harassment and doxxing.

## 2. Identity and verification

A Telegram Bot API `from_user.id` binds an account talking to the bot, not real-world identity, fund ownership or net worth. User-provided website/social links and purported check ranges are not proof by themselves. Manual review verifies only described criteria; badge wording cannot imply investment accreditation, capital verification or Telegram-issued verified check. If evidence needed, request minimum necessary and secure short-lived handling, no reflexive passport collection. Separate investor and founder review queues.

## 3. Contact privacy

Telegram handles optional and changeable. User chooses which specific contact to expose after accepted intent. Default private; plain like never reveals contact; only approved and consenting handoff or bot relay with block/report. Do not send phone/email automatically. Telegram `tg://user?id=...` personal mentions may fail due to privacy; no guaranteed direct link. Previously revealed contacts cannot be erased retroactively.

## 4. Moderation/safety

Human approval before startup publish and investor verified label; audit every decision with reason and revision. Report/block/appeal, transparent moderation policy, plagiarism/financial misinformation awareness, limits on mass contact/comment flood; separation of public review from private verification files. No fabricated rankings, fake social proof, fake subscribers, fake verified status.

## 5. Payments and financial activities

RAUNDO is discovery and introductions, **not fundraising custodial platform**. Does not handle equity transfers, investment deposits, escrow or income guarantees. **Digital subscription sales in Telegram** must follow Telegram Stars `XTR`, current Bot API pre-checkout, receipt, refund, support and local tax/receipt requirements. Before real payment verify Russian and provider rules, do not use alternative processors for in-Telegram digital goods. No payment to buy verification/ranking/contacts. Extra legal review before sponsored promotion or handling investment transactions.

## 6. Technical security

Admin bootstrap by private stable numeric Telegram IDs and server-side RBAC. DB authorization owner checks and no untrusted free text/callback authority; Telegram webhook secret validation; no global exposure of update payload. Strict file counts/limits 1–3 startup images, scan state, allowed URL protocols/safe redirects. Database transactions/idempotency, rate limit, report triage, outbox, unique votes. Encryption and backup/restore, secret rotation, least privilege. Public production bot commands omit /admin globally (scope-specific optional), but hiding /admin isn't security.

## 7. Data minimization / deletion

Terms, Privacy, consent versions before collection; explain exactly why requested data; published startup vs nonpublic investor proof/contacts. Secure storage region/legal localization review (Russia 152-FZ applicability), retention and deletion policy for proofs, support messages, payment records; export and request deletion under applicable law, with documented legal exceptions. Ensure bot can respond to /terms, /support and /paysupport when billing.

## 8. Launch blockers

- operator legal identity and hosting/PD review;
- real Terms/Privacy and consent recorded; support response process;
- approval of badge wording and review evidence;
- proper Stars settlement/recurring terms/refund/taxes;
- automated and manual security/privacy/regression QA;
- verified bot tokens/domain/webhook rules and real backups/alerts.
