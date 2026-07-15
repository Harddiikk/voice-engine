# Credits Unification — Single SaaS Ledger

**Date:** 2026-06-29
**Branch:** `feat/credits-unification`
**Status:** Approved design — ready for implementation plan

## 1. Context & Problem

The platform has **two** credit systems running side by side:

1. **LOCAL call-minute credits** — `organizations.free_call_seconds_remaining`
   (`1 credit = 60s`; trial grant `DEFAULT_FREE_CALL_SECONDS=1800`; `NULL = unlimited`;
   Razorpay INR top-ups via `api/routes/billing.py` → `add_call_seconds`). This is the
   active billing system on the Auto4You fork.
2. **UPSTREAM Dograh MPS "Model Credits"** — USD, `services.dograh.com`, hosted-only
   (`api/services/quota_service.py`, `workflow_run_billing.py`, `mps_billing.py`), gated
   by `DEPLOYMENT_MODE` (default `"oss"`). Currently **dormant**.

Two concrete defects follow from this:

- **Latent double-charge.** `api/tasks/workflow_completion.py` runs the local deduction
  (line 171, via `run_integrations.py:343 consume_free_call_seconds`) *and* the MPS USD
  charge (line 177, `report_workflow_run_platform_usage`) back-to-back off the **same**
  `usage_info.call_duration_seconds`. The only thing preventing a double-charge today is
  that `DEPLOYMENT_MODE` *happens* to default to `"oss"` (an unset env var). Flip it and
  every completed call bills twice.
- **Double pre-call gating + gating gaps.** Campaign start/resume and the public trigger
  call *both* the local `assert_has_free_call_seconds` and the MPS
  `authorize_workflow_run_start` (e.g. `campaign.py:557+561`, `:890+894`;
  `public_agent.py:280+344`). Meanwhile other billable entry points
  (`telephony.py:183`, `campaign_call_dispatcher.py:354`, inbound, `ari_manager.py:595`,
  `agent_stream.py:98`, `webrtc_signaling.py:429`, `workflow_text_chat.py:104`) have
  **only** the MPS gate — so once MPS is disabled they are ungated for credit exhaustion.

## 2. Goals / Non-Goals

**Goals**
- Make the local `free_call_seconds_remaining` ledger the single source of truth for billing.
- Hard-disable the MPS path behind a default-off flag; remove residual `services.dograh.com` calls.
- Gate and charge **every** runtime entry point exactly once.
- Eliminate the concurrent-overspend race for a bulk dialer via atomic reservation.
- Preserve `NULL = unlimited` everywhere.

**Non-Goals (separate workstreams, out of scope here)**
- Phone-number purchase pricing, agent-builder token metering, per-campaign budgets,
  de-Dograh branding, Airtable theme, final platform QA. Each gets its own spec.
  This spec is the foundation all the billing ones depend on.

## 3. Locked Decisions

| Decision | Choice |
|---|---|
| Source of truth | Local `free_call_seconds_remaining` (one shared wallet) |
| Dograh MPS | Killed entirely (default-off flag), not an either/or switch |
| What's billable | **Everything** — outbound, inbound, WebRTC live tester, text chat |
| Gating strictness | **Atomic reservation** + post-call reconcile |
| Wallet model | One shared balance for calls + numbers + agent-builder + campaign budgets |

## 4. Design

### A. Kill-switch for MPS
- Add `MANAGED_MODEL_SERVICES_ENABLED` (constants, env-driven, **default `False`**).
- `quota_service.authorize_workflow_run_start`: when disabled, return
  `QuotaCheckResult(has_quota=True)` before any MPS/legacy-Dograh-key work — this also
  removes the residual `services.dograh.com` calls in the legacy/oss-v2 paths
  (`quota_service.py:418-437`).
- `workflow_run_billing.report_workflow_run_platform_usage`: early-return when disabled
  (in addition to the existing `DEPLOYMENT_MODE=="oss"` guard).
- Pin `DEPLOYMENT_MODE=oss` explicitly on the VPS (`.env.api`) as belt-and-suspenders.
- Hide the `"dograh"` model **mode** in the model picker (`byok` only) so no new config can
  select a path that needs MPS. (The enum value `"dograh"` and `ServiceProviders.DOGRAH`
  stay — wire/DB contract — only the selectability changes.)

### B. Unified credit gate
A single service module (e.g. `api/services/credits/gate.py`) exposes the canonical gate,
replacing the scattered `assert_has_free_call_seconds` + `authorize_workflow_run_start`
double-gates:

- `reserve_credits(organization_id, est_seconds) -> Reservation | None`
  — atomic; `None` (or raises 402) when the metered balance can't cover `est_seconds`.
  No-op pass-through for `NULL`/unmetered orgs (returns an "unmetered" reservation).
- `reconcile(reservation, actual_seconds)` — refund `est - actual` (or charge the delta)
  after the run, leaving the ledger at the true consumed amount.

Built on the **already-present, race-safe** `try_charge_call_seconds` (atomic conditional
`UPDATE`, currently used only for number purchases) + `add_call_seconds` for refunds.

### C. Atomic reservation + reconcile (the chosen strictness)
1. **Before** a billable run: `reserve_credits(org, est_seconds)`. `est_seconds` =
   a conservative per-call estimate (config knob, e.g. `CREDIT_RESERVATION_SECONDS`,
   default ~ max expected call length). If the conditional `UPDATE` fails → reject (402 /
   skip dispatch / pause campaign).
2. **After** the run (in `process_workflow_completion`): `reconcile(reservation, actual)`
   using `usage_info.call_duration_seconds`. Refund unused seconds; if the call ran longer
   than reserved, deduct the overage (best-effort, floored at 0).
3. This closes the race where N concurrent campaign calls all pass a non-atomic balance read.

### D. Exactly one post-call charge
- `run_integrations.py:343 consume_free_call_seconds` is folded into `reconcile` — it
  remains the **sole** ledger mutation for call usage.
- `workflow_completion.py:177` MPS report goes silent via the flag (A).
- Net effect: one reservation at start, one reconcile at end. No second ledger touches the call.

### E. Entry points (apply the gate at all of them)

| Surface | File:line | Today | After |
|---|---|---|---|
| Outbound /run | `telephony.py:183` | MPS-only | reserve_credits |
| Campaign dispatch (per call) | `campaign_call_dispatcher.py:354` | MPS-only | reserve_credits |
| Inbound | `telephony.py:778`, `:913`, `ari_manager.py:595` | MPS-only | reserve_credits |
| Agent stream | `agent_stream.py:98` | MPS-only | reserve_credits |
| WebRTC live tester | `webrtc_signaling.py:429` | MPS-only | reserve_credits |
| Text chat | `workflow_text_chat.py:104` | MPS-only | reserve_credits |
| Campaign start/resume | `campaign.py:557`, `:890` | double-gate | single gate |
| Public trigger | `public_agent.py:280`, `:344` | double-gate | single gate |

### F. Text-chat & WebRTC metering
Both are "billable: everything." They bill by **session wall-clock minutes** (same
`1 credit = 1 min`). Verify `pipeline_metrics_aggregator` emits a duration for these
non-telephony sessions; if a text-chat session has no `call_duration_seconds`, add a
session-duration writer so `reconcile` has an `actual` to settle against.

### G. Shared wallet semantics
- Calls, phone-number setup (`NUMBER_SETUP_MINUTES`), agent-builder generations, and
  per-campaign budgets all draw the **same** `free_call_seconds_remaining`.
- `NULL = unlimited` is preserved everywhere: unmetered orgs are never reserved-against or
  charged, on any surface. Top-ups must never credit a `NULL` org (already guarded).
- Razorpay top-up (`billing.py:129 add_call_seconds`) credits the one balance — unchanged.

## 5. Data Model
No schema change for unification itself — it reuses `organizations.free_call_seconds_remaining`.
(Per-campaign budget adds its own column in its own spec.)

## 6. Precondition / Safety Checks (run before shipping the kill-switch)
Killing MPS also removes the correlation-id mint that `mode="dograh"` configs need for model
**access** (not just billing). Before enabling the kill-switch in prod, confirm no workflow
is on Dograh-managed models:

```sql
-- expect 0 rows
SELECT id, organization_id FROM workflows
WHERE workflow_configurations::text LIKE '%"mode":"dograh"%' LIMIT 20;
```
If rows exist: migrate them to `byok`, or keep MPS as an either/or switch for those orgs
instead of a hard kill. New orgs default to `byok` (`ai_model_configuration.py:287/305`),
so this is expected to be empty.

## 7. Risks & Mitigations
| Risk | Mitigation |
|---|---|
| Latent double-charge | Kill-switch (A) makes MPS post-call report a no-op |
| Inbound/text ungated after MPS off | Single gate applied at all entry points (E) |
| Concurrent overspend (bulk dialer) | Atomic reservation via `try_charge_call_seconds` (C) |
| Reservation leak (run dies before reconcile) | Reconcile in completion task + a sweep that refunds reservations for runs stuck/failed past a TTL |
| `mode="dograh"` orgs lose model access | Precondition check (6) before enabling flag |
| `NULL`→metered conversion via top-up | Existing guard preserved; covered by tests |
| `test_quota_service` asserts on MPS strings | Update expectations as part of the change |

## 8. Test Plan
- Kill-switch off → `authorize_workflow_run_start` returns `has_quota=True`,
  `report_workflow_run_platform_usage` no-ops (no `services.dograh.com` calls).
- `reserve_credits`: metered org with sufficient / insufficient balance; `NULL` org passes
  free; concurrent reservations never oversell (simulate N parallel reserves).
- `reconcile`: refund on short call, overage on long call, floored at 0.
- Each entry point rejects at zero balance and charges once on success.
- Text-chat/WebRTC session produces a duration and reconciles.
- Regression: Razorpay top-up still credits and never converts a `NULL` org.

## 9. Open Items
- Exact `CREDIT_RESERVATION_SECONDS` default (per-call reservation estimate).
- Whether text-chat bills by wall-clock minute or a smaller unit (confirm during planning
  once we read the text-chat metrics path).
- Reservation-sweep TTL for orphaned reservations.
