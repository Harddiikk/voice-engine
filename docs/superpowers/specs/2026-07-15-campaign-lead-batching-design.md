# Campaign Lead Batching, Sheet Reuse & Phone Normalization — Design

**Date:** 2026-07-15 · **Requested by:** Hardik (for GPC do.gpconline.in) · **Repo:** voice-engine (Dograh fork)

## Goal

Upload a sheet of ~2,000 contacts once, then call it down in operator-chosen slices:
"call the next 100 today", come back tomorrow, "call the next 200" — same campaign,
persistent position, no re-upload. Plus: reuse an already-uploaded sheet in new
campaigns, auto-normalize Indian numbers to +91, and dedupe numbers (exists — keep).

## Feature 1 — Lead quota with persistent cursor ("call next N")

**Model.** No schema change. Two keys in `campaigns.orchestrator_metadata` (same
pattern as `budget_seconds`/`consumed_seconds`):
- `lead_quota` (int | null) — N for the current run window; null = unlimited (today's behavior).
- `lead_quota_used` (int) — first-attempt dispatches counted in this window.

The **cursor is the existing `queued_runs.state`** (`queued` = not yet called,
`processed` = called). "Next N" = first N still-`queued` rows. To make "next"
deterministic, claim ordering changes from `ORDER BY random()` to `ORDER BY id`
(sheet order preserved end-to-end since `source_uuid` pins original row position).

**Gating.**
- `process_batch` checks quota before dispatch (mirrors `campaign_budget_exhausted`
  gate at dispatcher.py:102): quota reached → campaign auto-**pauses** with log
  `"Lead quota of N reached — M leads remaining. Resume with a new quota for the next batch."`
- `claim_queued_runs_for_processing` claims `min(batch_size, quota_remaining)` new
  leads so a batch never overshoots.
- **Retries don't consume quota** (they're re-dials of leads already counted);
  scheduled retries are claimed first, as today. Retry storms are already capped by
  `retry_config.max_retries`.

**API.**
- `POST /campaign/{id}/start` and `/resume` accept optional JSON body
  `{"call_limit": N}` → sets `lead_quota=N`, resets `lead_quota_used=0`.
  No body / null → clears quota (unlimited), preserving current behavior.
- `CampaignResponse` gains `call_limit`, `calls_made_in_window`, `leads_remaining`
  (count of `queued` runs with `retry_count=0`).

**UI.**
- Create page (`campaigns/new`): "Leads to call in this run" numeric input (optional)
  in `CampaignAdvancedSettings`, next to `budgetMinutes`; passed to `/start`.
- Campaign detail page (`campaigns/[id]`): when paused with leads remaining, a
  "Call next ___ leads" input + button → `/resume {call_limit: N}`. Shows
  `X called · Y remaining`.

## Feature 2 — Sheet reuse across campaigns

`source_id` is just an object-storage key; `source_uuid` uniqueness is scoped per
campaign, so pointing a new campaign at an old key already works.
- New endpoint `GET /campaign/sources`: distinct prior CSV sources for the org —
  `{source_id, filename, total_rows, first_used_at, campaigns_count}` (derived from
  the campaigns table, newest first, capped 50).
- Create page: "Contact sheet" section becomes **dropdown**: `Upload new sheet` /
  previously uploaded sheets. Picking an existing one skips upload and feeds the
  existing `preview-csv` + column-mapping flow with that key.

## Feature 3 — +91 normalization hardening

`normalize_phone_number` (source_sync.py:107) already prepends `default_country_code`
(+91 default). Bug fixed by this design: a bare `91XXXXXXXXXX` (12 digits already
carrying the country code, common in Indian exports) currently becomes
`+9191XXXXXXXXXX`. New rule, applied when `default_country_code=+91`-style codes:
if the digit string equals `<cc><national number>` and is `len(cc)+10` long,
prefix `+` instead of double-prefixing. Also handles `0091…` → `+91…`.
Dedupe continues to run on normalized numbers, so `98765 43210`, `09876543210`,
`919876543210` and `+919876543210` all collapse to one lead.

## Feature 4 — Dedupe

Already implemented (`dedupe_by_phone_number`, `duplicates_removed` in API + UI toast,
DB unique constraint backstop). No change beyond benefiting from Feature 3.

## Out of scope

Cross-campaign dedupe ("don't call anyone called by another campaign"), sheet
editing, per-batch reporting beyond existing runs filters.

## Testing

- Unit: quota helpers; claim ordering + quota cap; auto-pause at quota; resume resets
  window; normalization cases (10-digit, 0-prefix, 91-prefix, 0091, +91, junk).
  Copy patterns from `test_campaign_budget.py`, `test_campaign_csv_sync_dedup.py`,
  `test_campaign_call_dispatcher.py` (real-Postgres fixture for claim logic).
- Flow: seed campaign with 20-row CSV (with dups + mixed formats), start with
  `call_limit=5` → exactly 5 dispatched then paused; resume with 5 → next 5 in sheet
  order; dups removed; numbers all `+91…`.

## Deployment

Build/tag fork images → GPC VPS `/opt/apps/dograh` (SSH currently gated). Do not
restart containers while a campaign is mid-run.
