# Full Admin Section — Client Management (design)

_Created 2026-07-03. Auto4You / voice-engine (feat/voicelink-saas)._

## Context

The owner is a reseller onboarding client orgs (Optik, Investors Propmart, …).
The current admin `/clients` page (superuser-only) can list orgs and: grant
credits, provision VoiceLink, reveal/record the client's VoiceLink password,
assign a DID, check KYC, and impersonate. It **cannot** change a client's plan,
set custom prices, charge a setup fee, add notes, suspend a client, or show
money in ₹ — all of which the owner needs to run the business. This spec designs
the full admin section on top of what exists.

## Locked decisions (owner, 2026-07-03)

1. **Prepaid credits** — clients load credits; every call, number, and setup fee
   deducts from the balance. "Money spent" = credits consumed × the client's
   rate. No invoicing/owed-balance system.
2. **Every client can have custom pricing** — each org may set its own ₹/min
   rate, number price, and setup fee, defaulting to the public pack rates.
3. **Admin plan override wins** — an admin-set plan overrides the
   purchase-derived plan and drives feature gates.

## Data model (additive — no risky migration)

All admin-managed per-client settings live in ONE new `org_configurations`
JSON record under a new key `OrganizationConfigurationKey.ADMIN_PROFILE`
(mirrors CONCURRENT_CALL_LIMIT / MODEL_CONFIGURATION_V2 / ONBOARDING_PROFILE):

```jsonc
{
  "plan_override": "scale" | null,        // wins over get_org_plan's derived tier
  "pricing": {
    "per_minute_inr": 8.0 | null,         // null → plan/global CAMPAIGN_SPEND_RATE_INR_PER_MINUTE
    "number_price_inr": 500 | null,       // null → global NUMBER_PRICE_INR
    "setup_fee_inr": 0 | null             // one-time; null/0 → none
  },
  "suspended": false,                     // true → blocks dialing (gate)
  "notes": [ { "at": "ISO", "by": <user_id>, "text": "…" } ]  // append-only ops log
}
```

- **Notes** as a bounded JSON list is fine for an ops log; if volume grows it can
  move to a dedicated table later (noted, not built now).
- **Setup-fee charge** is a `credit_ledger` row `kind="setup_fee"` (new kind):
  seconds = `round(setup_fee_inr / per_minute_inr * 60)` deducted via the
  existing `charge_purchase_tx` primitive.
- **Audit log**: a new lightweight `admin_audit` table (id, actor_user_id,
  target_org_id, action, detail JSON, created_at) — every admin mutation writes
  one row. High value for a real admin panel; additive.
- **Enterprise tier**: add `"enterprise"` to `PLAN_RANK` (top) and to
  `features_for_plan` (all features on). The owner's Enterprise card already
  exists; this makes it a real assignable tier.

## Resolution helpers (api/services/plans.py + a new pricing module)

- `get_org_plan(org)` → if `plan_override` set, return it; else current derived
  logic. `features_for_plan(effective_plan)` unchanged (add enterprise).
- New `get_org_pricing(org) -> {per_minute_inr, number_price_inr, setup_fee_inr}`
  — per-client override else global constant. Consumed by:
  - campaign spend display (replaces the flat `CAMPAIGN_SPEND_RATE_INR_PER_MINUTE`),
  - number purchase charge (replaces flat `NUMBER_PRICE_INR`),
  - the ₹ money display.
- `is_org_suspended(org)` — checked at the same gate points as KYC
  (campaign start/resume, public trigger, buy-number): suspended → 403
  "This account is suspended. Contact your administrator."

## Money in ₹ (left + spent)

Credits are seconds; ₹ = minutes × the client's `per_minute_inr`.
- `money_left_inr = balance_seconds / 60 * per_minute_inr`
- `money_spent_inr` = sum of debit ledger rows (settle_charge, number_purchase,
  setup_fee, negative adjustments) in seconds / 60 * per_minute_inr.
  Simplification: computed at the CURRENT rate (historical rate drift is
  acknowledged and acceptable for MVP; a future improvement is stamping ₹ on
  each ledger row).
- Shown on: the client's `/credits` page (₹ balance + ₹ spent alongside credits)
  and every admin client row + detail.

## Backend endpoints (extend api/routes/admin_clients.py, all superuser)

- `GET /admin/clients` (existing) — list gains: `effective_plan`,
  `money_left_inr`, `money_spent_inr`, `per_minute_inr`, `suspended`.
- `GET /admin/clients/{org}` — NEW per-client detail: profile (plan/pricing/
  suspended/notes) + credits (₹) + VoiceLink state + KYC + a usage summary
  (total calls, minutes, spend from the overview aggregation).
- `PATCH /admin/clients/{org}/profile` — NEW set plan_override / pricing /
  suspended (partial update; audited).
- `POST /admin/clients/{org}/notes` — NEW append a timestamped note (audited).
- `POST /admin/clients/{org}/charge-setup-fee` — NEW deduct the setup fee via
  `charge_purchase_tx(kind="setup_fee")` (audited; 409 on unmetered/insufficient).
- `POST /admin/clients` — NEW create org + owner user + optional starting
  credits + optional VoiceLink provision (composes existing signup/provision).
- `GET /admin/audit?org=&limit=` — NEW admin action log.
- Existing (unchanged): retry-provision, create-client, assign-did,
  grant-credits, password get/post, kyc-status, impersonate.

## UI

- **/clients list** (`ui/src/app/clients/page.tsx`): add Plan, ₹ Balance,
  ₹ Spent, Suspended columns; search + filter (plan, low-balance, suspended,
  KYC). Row → detail page.
- **/clients/[orgId]** NEW detail page with tabs:
  - **Overview** — status, plan, ₹ balance + spent, KYC, VoiceLink client/DID,
    quick actions.
  - **Billing** — grant/add credits, change plan (select), custom pricing
    (₹/min, number price, setup fee), "Charge setup fee" button, full ledger.
  - **VoiceLink** — provision, password reveal/record, assign DID, KYC (exist).
  - **Usage** — calls/minutes/spend + link to their runs.
  - **Notes** — append-only admin ops log.
  - **Danger zone** — suspend/unsuspend, (delete deferred).
- **/credits** (client-facing, `CreditsSection.tsx`): ₹ balance + ₹ spent
  alongside the credit figures.

## Phasing (each ships + deploys independently)

1. **₹ display** — `get_org_pricing` + money fields on balance/list + Credits UI.
   Small, immediate, uses existing ledger.
2. **Billing core** — ADMIN_PROFILE config, plan override, custom pricing wired
   into spend + number purchase, setup-fee charge, PATCH profile endpoint.
3. **Ops** — notes, suspend gate, audit log.
4. **Detail page + create-client** — the `/clients/[orgId]` drill-in wrapping
   everything, and admin create-client.

## Verification

- Unit: `get_org_plan` override precedence; `get_org_pricing` fallback;
  suspend gate 403; setup-fee ledger row; money_left/spent math; profile PATCH
  round-trip; audit rows written.
- Prod: set a custom rate on a test org → its ₹ balance/spent reflect it;
  assign a plan → features change; charge a setup fee → balance drops + ledger
  row; suspend → campaign start 403s; unsuspend → works.
- No migration except the additive `admin_audit` table (alembic single-head gate).

## Out of scope (deferred)

Invoicing/owed-balance (prepaid only), per-ledger-row ₹ stamping, client
delete/hard-offboard, notes-as-table, multi-admin roles/permissions beyond
superuser.
