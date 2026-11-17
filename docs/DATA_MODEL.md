# RAUNDO — целевая модель PostgreSQL и договоры целостности

**Это blueprint; SQLAlchemy/Alembic модели реализуются последовательно.** No Mini App entities needed.

## 1. Identity, roles and consents

```text
users(id UUID PK, telegram_id BIGINT UNIQUE NOT NULL, username nullable, first_name nullable,
      locale, status, created_at, last_seen_at, deleted_at?)
user_consents(id, user_id, kind, policy_version, granted_at, revoked_at?)
user_profiles(user_id UNIQUE, display_name, bio, contact_mode, contact_username_nullable, show_contact_after_accept BOOLEAN)
user_roles(user_id, role_code, assigned_at, revoked_at?) -> roles viewer/founder/investor/staff separate from verified
admin_roles(id, user_id, permission_code, created_by, revoked_at?)  # grant by trusted server bootstrap
```

Telegram `username` mutable/nullable. Telegram `user_id` proves channel account control only; "verified" is manual review status, never Telegram blue check.

## 2. Investor full profile/review

```text
investor_profiles(id, user_id UNIQUE, display_name, investor_type, organization_name?,
                  position?, strategy_description, min_check_minor?, max_check_minor?,
                  currency?, geography jsonb, public_reference_url?, contact_policy,
                  review_status, current_revision, approved_at?, approved_by?, revoked_at?)
investor_sector_preferences(investor_id, sector_id, PRIMARY KEY(investor_id,sector_id))
investor_stage_preferences(investor_id, stage_id, PRIMARY KEY(investor_id,stage_id))
investor_evidence(id, investor_id, restricted_storage_key, purpose, expires_at, created_at)
investor_reviews(id, investor_id, revision, state, reviewer_user_id?, decision_reason?,
                 requested_proofs?, created_at, decided_at?)
```

At least one sector + one stage enforced **both input & server-side submit**. No `verified_investor` via paid entitlements. Pending/rejected investors can interact as community, never count in investor leaderboard or display verified label. Review audit immutable.

## 3. Startups, required 1–3 photos, founder verification

```text
startups(id, owner_user_id, slug UNIQUE, name, one_liner, full_description, primary_sector_id,
         stage_id, website_url?, app_url?, demo_url?, no_link_reason?, intents jsonb,
         ask_amount_minor?, ask_currency?, country?, review_state, current_revision, published_at?)
startup_sectors(startup_id, sector_id, is_primary, UNIQUE(startup_id,sector_id))
startup_members(id, startup_id, user_id, role, claim_status, invited_at, joined_at?)
startup_media(id, startup_id, telegram_file_id, file_unique_id, position 1..3, kind, mime,
              width, height, size, scan_state, visibility, created_at)
founder_affiliation_reviews(id, startup_id, user_id, review_revision, evidence_ref?,
                            outcome, reviewer_id?, reason?, decision_at?)
startup_reviews(id, startup_id, revision, state, reviewer_id?, reason_code?,
                comment?, submitted_at, decided_at?)
sectors(id, code UNIQUE, name_ru, name_en, active, taxonomy_version)
stages(id, code UNIQUE, display_order, label, active)
startup_claims(id, startup_id, claim_type, value, source, as_of, verified_state)
```

Submit transaction checks required pitch/description/category/stage/representative/contact policy and **1 <= count(usable_public_photo) <= 3**. PostgreSQL check via service transactional lock/trigger where appropriate; media position unique(startup_id,position); files must be owner authorized. Pending review projects not feed or indexed publicly. Manual owner affiliation and public listing review tracked separately.

## 4. Feed, social, requests and contact

```text
feed_impressions(id, user_id, startup_id, timestamp, qualified, source, dedupe_key UNIQUE)
startup_reactions(user_id, startup_id, kind like|dislike, created_at, updated_at,
                  PRIMARY KEY(user_id,startup_id))
startup_skips(user_id, startup_id, skipped_at, PRIMARY KEY(user_id,startup_id))
startup_bookmarks(user_id, startup_id, created_at, PRIMARY KEY(user_id,startup_id))
startup_follows(user_id, startup_id, created_at, PRIMARY KEY(user_id,startup_id))
comments(id, startup_id, user_id, parent_id?, text, review_state, created_at, edited_at?, deleted_at?)
contact_requests(id, startup_id, requester_id, representative_user_id, intent, message, state,
                 requested_at, decided_at?, expires_at?, requester_consent, founder_consent,
                 contact_channel, idempotency_key UNIQUE)
contact_request_events(id, request_id, actor_user_id, old_state, new_state, created_at)
relay_messages(id, request_id, sender_user_id, destination_user_id, text?, restricted_attachment_id?,
               moderation_status, delivered_at?, created_at)
blocks(blocker_user_id, blocked_user_id, created_at, PRIMARY KEY(blocker_user_id,blocked_user_id))
```

Contact material NEVER exposed in public startup projections, login logs or ranking snapshots. A profile's allowed contact disclosure policy must be checked again on every accept; unverified investor cannot falsify verified identity. Max one active request per requester/startup/intent by DB guard with cooldown.

## 5. Rankings

```text
ranking_periods(id, start_at UTC, end_at UTC, algorithm_version, state, UNIQUE(start_at,end_at))
ranking_eligibility(period_id, cohort, user_id, startup_id, eligible, reason?, score_basis)
ranking_snapshots(id, period_id, cohort community|investor, rank, startup_id,
                  score, voter_count, confidence, revision, published_at,
                  UNIQUE(period_id,cohort,rank,revision),
                  UNIQUE(period_id,cohort,startup_id,revision))
```

No PRO/entitlements columns in scoring. Eligibility investor status checked per defined snapshot method; self/team votes excluded. Snapshot immutable after publish except named corrective revision with reason.

## 6. Support/admin/moderation

```text
support_tickets(id, public_number UNIQUE, requester_user_id, category, linked_startup_id?,
                linked_investor_id?, status, priority, assignee_user_id?, opened_at, closed_at?)
support_messages(id, ticket_id, actor_user_id, visibility public|internal, text,
                 telegram_update_id? UNIQUE, delivery_status, created_at)
support_attachments(id, message_id, restricted_file_ref, size, mime, scan_state, retention_at?)
reports(id, reporter_user_id, target_type, target_id, reason, state, created_at)
appeals(id, target_action_id, applicant_user_id, reason, status, decision_at?)
moderation_actions(id, actor_user_id, action, target_type, target_id, before_state,
                   after_state, reason_code, notes_safe, created_at)
admin_audit_events(id, actor_user_id, action, resource_kind, resource_id, metadata_redacted, ts)
```

Internal staff notes must never appear in user ticket read, bot notifications or data export of third parties.

## 7. Premium and delivery

```text
plans(id, code, audience, features_json, active, plan_version)
plan_prices(id, plan_id, currency XTR, amount_integer, billing_period, active_from)
orders(id, user_id, plan_price_id, telegram_payload UNIQUE, expected_amount, status, created_at)
payment_events(id, telegram_charge_id UNIQUE, order_id, event_kind, processed_at, safe_hash)
subscriptions(id, user_id, plan_code, state, expires_at, cancel_at_period_end)
entitlements(id, user_id, feature_code, source, expires_at)
outbox_events(id, kind, recipient_user_id, resource_type, resource_id, dedupe_key UNIQUE,
              safe_payload, state, next_attempt_at)
bot_deliveries(id, outbox_id, telegram_message_id?, attempt_count, status, error_code)
processed_updates(update_id BIGINT PRIMARY KEY, created_at)
analytics_events(id, user_key_pseudonym, event_name, properties_safe, timestamp)
```

Money in Stars integer; investment asks in fiat *minor units* with currency. No floating point currencies.

## 8. Security and migrations

DB foreign keys, check/unique constraints, locking for submit/approval/payment, stable UUID IDs, UTC timestamps, proper btree indexes for investor_sectors, startups(review_state,sector,stage,published_at), requests(state,recipient), tickets(status,updated), outbox(state,next_attempt). Use Alembic migrations, snapshot tests and backup plan. No destructive migrations without approval; data export/deletion and retention follow law.

## 9. State machines

`startup`: draft → submitted → under_review → approved/published | changes_requested | rejected; published → suspended | archived, resubmission by revision.

`investor`: draft → submitted → under_review → approved | changes_requested | rejected; approved → suspended/revoked, resubmission documented.

`contact`: pending → accepted | declined | withdrawn | expired; accepted → closed | blocked; consent gates contact projection.

`ticket`: open → in_progress → waiting_user → resolved → closed; reopen policy.

`order`: created → pending → paid | failed | expired; paid → refunded; subscription entitlement updated through idempotent verified events.

## NEW CANON: funding organizations, member affiliation and differentiated funding modes (2026-10-22)

Existing `investor_profiles` is the initial person-only model; do NOT assume `organization_name` string verifies organizational affiliation. Target expansion (migration and legacy compatibility needed):

```text
funding_organizations(id UUID, canonical_name, organization_type, verified_domain?, country?,
                      official_url?, description?, status, reviewed_at?, reviewing_admin_id?,
                      direct_investment_mandate BOOLEAN NOT NULL DEFAULT false)
funding_org_support_modes(organization_id, support_mode_code, PRIMARY KEY(organization_id,support_mode_code))
funding_org_sectors(organization_id, sector_id, PK(...)); funding_org_stages(organization_id, stage_id, PK(...))
organization_memberships(id UUID, organization_id, user_id, role_code, membership_status,
                         permission_scope, claimed_at, verified_at?, verified_by?, revoked_at?,
                         UNIQUE(organization_id,user_id,role_code))
funding_org_reviews(id, organization_id, revision, decision, reviewer_id, reason,
                    evidence_private_ref?, created_at)
organization_membership_reviews(id, membership_id, revision, reviewer_id, state, reason, created_at)
participant_support_modes(profile_id, support_mode_code, PK(...))
participant_mandates(id, profile_id, organization_id?, directly_invests BOOLEAN, lead_round BOOLEAN?,
                     accepts_open_applications BOOLEAN?, cheque_min_minor?, cheque_max_minor?, currency?,
                     verified_scope, reviewer_id?, status)
programs(id, organization_id, kind accelerator|incubator|grant|pilot, eligibility_json,
         apply_url?, opens_at?, closes_at?, intake_status, last_verified_at?)
```

Add staff-verification/audit and consistency constraints, e.g. **only organization-approved representative can submit a request on behalf of org**, member grant actions server-side RBAC and revocable. Organisational `organization_type` != participation funding `support_mode`: seed/sector are *preferences*, not new entity identity. Existing `investor_type` legacy values (`fund`, `corporate` etc) require an explicit backwards-compatible mapping, no destructive migration without reviewed plan. Fund votes remain one per human actor, not one per membership or org. Funding programs/grants never automatically enter verified investor vote cohort. Spec at `docs/FUNDING_PARTICIPANTS.md`.
