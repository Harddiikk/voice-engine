# Credits & Billing System — Architecture Recommendation for the VoiceLink Voice‑Agent SaaS

**Author:** Lead Architect · **Stack:** FastAPI + Next.js + Postgres (SQLAlchemy async) + ARQ + Razorpay · **Target branch:** `feat/voicelink-saas`

---

## 1. Executive Summary & Recommendation

**Recommendation: build "Reserve‑Meter‑Reconcile v2" (RMR‑2) — a transaction‑safe, append‑only call‑seconds ledger that promotes the repo's already‑race‑safe `try_charge_call_seconds` primitive from number‑purchases to per‑call billing.** RMR‑2 takes the highest‑scoring candidate on fit (Design 1, *Reserve‑Meter‑Reconcile*, judge 7.6, fitWithExisting 9) as its backbone — keep the single `organizations.free_call_seconds_remaining` seconds balance, keep Razorpay, keep `1 credit = 1 minute = 60s`, reserve credits as a *real up‑front debit* with **no holds table**, hard‑cap each call's `max_call_duration` to its reservation, and refund the remainder post‑call — and then grafts the two non‑negotiable correctness fixes the judges demanded: **(a)** make *charge + ledger‑row + settled‑flag a single Postgres transaction* (the idempotency keys in Design 1 don't actually protect the money otherwise, because today's `try_charge_call_seconds`/`add_call_seconds` each open and commit their *own* session — verified at `api/db/organization_client.py:95,71`), and **(b)** make the post‑call reconcile **idempotent and entrypoint‑agnostic** so inbound/non‑dispatcher runs are never left on the old swallow‑and‑grant‑free path. From Design 2 (highest correctness, 9) we adopt the UNIQUE‑keyed append‑only ledger + cached‑balance + nightly reconciliation + Razorpay webhook convergence; from Design 3 (highest voiceFit, 9) we adopt the no‑op‑by‑default margin layer (`BILLING_MULTIPLIER`, `BILLING_MINIMUM_SECONDS`, per‑model burn multipliers) and the "hide the MPS UI, one meaning of *credit*" cleanup. We **do not** add Stripe, **do not** adopt a separate escrow/holds table (its orphaned‑hold leak and double‑counting invariant are the parts all three judges said to cut), and **do not** re‑meter STT/TTS/LLM into the balance for v1. The result is correct‑by‑construction on the four confirmed fatal gaps (concurrency overrun, mid‑call exhaustion, silent free‑calls‑on‑error, double‑decrement‑on‑retry), ships zero‑downtime via one additive nullable migration, and lands in ~6–8 engineer‑days with a Phase‑0 money‑leak fix shippable on day one.

---

## 2. How VAPP Does Credits (Copy / Avoid)

VAPP's "billing" is a **decorative wallet**: a per‑user monetary `balance` (`models/credits.ts`) that is **only ever incremented** by a manual top‑up guarded by a hardcoded shared password (`"Vapp@7349"`, `credits.controller.ts:13`). A grep across the whole backend finds **zero** balance‑subtraction code — the `'debit'` transaction type is declared but never instantiated, calls are never gated by funds, and STT/TTS/LLM are paid out‑of‑band via shared provider keys. Per‑minute cost (`₹10`) is computed **purely for dashboard display**, never deducted.

| Copy from VAPP | Avoid from VAPP |
|---|---|
| Clean layering (schema → data‑access lib → controller → routes) | **No deduction at all** — the wallet cannot gate usage (the fatal flaw) |
| Append‑only `credit_transactions` ledger *shape* (type/status/reference modeled for a gateway) | **No payment gateway** — top‑ups minted by anyone with a static password |
| Atomic `$inc` for balance mutation (race‑safe top‑ups) | **Non‑atomic, non‑idempotent top‑up** (record → `$inc` → re‑read; retries double‑credit) |
| `autoTopup` *schema* (threshold/amount) as a forward‑looking shape | `autoTopup` is **dead config** — stored, never executed |
| Per‑user/per‑direction rate overrides (pricing flexibility w/o code change) | **Currency‑mixing bug** (INR & USD `$inc` into one meaningless figure) |
| Defensive multi‑source call‑duration extraction | **Multiple contradictory cost models**; comments disagree with code |

**Net:** VAPP validates the *ledger primitive* and the *auto‑recharge concept*, but is an anti‑pattern for everything that matters (deduction, gateway, idempotency, balance‑vs‑ledger reconciliation). We take the shape, not the substance.

---

## 3. The Voice‑Engine's CURRENT Credits System (Verified) — What Exists & Its Gaps

The fork runs **two overlapping systems**. We are improving **(A) the local "VoiceLink" call‑seconds ledger**; **(B) the upstream Dograh MPS remote credit system** is `DEPLOYMENT_MODE != "oss"` only (default is `"oss"`, verified `api/constants.py:44`) and we deliberately leave it untouched.

**What exists today (all file references verified in‑repo):**

- **Balance:** `organizations.free_call_seconds_remaining` — `Integer, nullable` (`api/db/models.py:158`). `NULL = unlimited`; non‑null = metered remaining **seconds**. `1 credit = 1 minute = 60s`.
- **Pre‑call gate (NOT a reservation):** `has_free_call_seconds` = "`balance is None or balance > 0`" (`api/services/trial_credits.py:23`); `assert_has_free_call_seconds` raises **HTTP 402** (`trial_credits.py:29`) at campaign start (`campaign.py:557`), resume (`campaign.py:890`), public trigger (`public_agent.py:344`); the dispatcher pauses a campaign when empty.
- **Post‑call debit (best‑effort):** Step 6c in `api/tasks/run_integrations.py` reads `usage_info["call_duration_seconds"]` (verified at lines ~333‑347) and calls `consume_free_call_seconds` → `decrement_free_call_seconds` → `UPDATE ... SET free_call_seconds_remaining = GREATEST(balance - seconds, 0) WHERE balance IS NOT NULL` (`organization_client.py:47`). It **swallows all exceptions** (`trial_credits.py:49`).
- **The one good primitive (unused for calls):** `try_charge_call_seconds` (`organization_client.py:95`) — a single atomic conditional `UPDATE ... WHERE balance >= seconds`, genuinely race‑safe, currently used **only** for number purchases (`telephony_marketplace.py`).
- **Payments:** Razorpay platform flow in `api/routes/billing.py` — `/order` (61) creates a `PaymentTransactionModel`, `/verify` (106) recomputes HMAC and is **idempotent on `txn.status == "paid"`** (114), then `mark_transaction_paid` (126) + `add_call_seconds` (129). `CREDIT_PACKS` at `constants.py:81`; 30‑min trial grant `DEFAULT_FREE_CALL_SECONDS=1800` (`constants.py:68`).

**Concrete gaps (the four fatal + three structural):**

| # | Gap | Evidence | Consequence |
|---|---|---|---|
| 1 | **No reservation** — gate is only `balance > 0` | `trial_credits.py:23` | A call can start with 1s of balance and run minutes; overage floored to 0 = lost revenue |
| 2 | **Concurrency overrun by design** | dispatcher "bounds overrun to at most one batch" | N concurrent calls all pass the check‑then‑act gate on a tiny balance |
| 3 | **No mid‑call exhaustion handling** | `max_call_duration` fixed 300s (`run_pipeline.py:361`), unrelated to balance | One long call vastly exceeds remaining credits |
| 4 | **Double‑decrement on ARQ retry** | no `billed` flag on the run; `consume_free_call_seconds` inside retryable job | A retried `process_workflow_completion` deducts twice; `GREATEST(.,0)` hides it |
| 5 | **Best‑effort → silent FREE call** | `trial_credits.py:49` swallows all | A DB hiccup at decrement = a completely free call |
| 6 | **No debit ledger / no reconciliation** | only top‑ups journaled (`payment_transactions`) | Can't attribute a charge to a run, refund a bad call, or detect drift |
| 7 | **No Razorpay webhook** | only `/verify` credits | Captured payment + closed tab = txn stuck `created`, uncredited forever |
| 8 | **Two divergent "credit" UIs** | `/credits` (real) vs `/billing` MPS stub that throws "coming soon" | User/model confusion over what "credit" means |
| 9 | **`add_call_seconds` NULL footgun** | `COALESCE(balance,0)+seconds`; "Callers MUST" guard by convention (`organization_client.py:71`) | One unguarded call converts an unlimited org to metered |
| 10 | **Margin gap** | flat `1 credit/min`, ignores `cost_info.charge_usd` (`models.py:577`) | Expensive‑model and cheap calls cost the same; packs at/below COGS |

---

## 4. How Competitors Meter & Price — and What We Should Charge

| Platform | Metering model | Headline | True all‑in | Reusable lever |
|---|---|---|---|---|
| **Vapi** | Platform fee + passthrough (BYO‑key) | $0.05/min | $0.14–0.33/min | Thin fee + BYO‑key for power users |
| **Retell** | Modular per‑minute add‑up (line items) | ~$0.11–0.15/min | $0.11–0.24 | **Per‑model LLM upcharge** (Gemini Flash $0.035 → GPT‑5.5 $0.16); 20 free concurrent, +$8/line |
| **Bland** | **Bundled single connected‑minute**, tiered fee → lower rate | $0.11–0.14/min | bundled | **One blended rate hides the split**; grace overdraft; tiered MRR |
| **ElevenAgents** | Subscription incl. minutes + overage | $0.08 overage / **$0.16 burst (2×)** | + LLM/tel | **Included‑minute bundles** + **2× burst over concurrency** |
| **Synthflow** | Modular + **Stripe rebilling for agencies** | $0.09 voice engine + LLM/tel | $0.11–0.24 | **Reseller blueprint**: set your own plan/included‑min/overage |
| **Telnyx** | Near‑bundled, owns stack | **$0.05/min all‑in** | $0.05–0.08 | Cheapest credible supplier; **in‑portal cost estimator** |
| **Deepgram / Cartesia / Twilio** (components) | Per‑min / per‑char | STT ~$0.008, TTS ~$0.025, SIP $0.004 | — | **COGS floor** |

**COGS floor (lean self‑built stack):**

| Component | Cost/min |
|---|---|
| Deepgram STT | ~$0.008 |
| Cartesia / Aura‑2 TTS | ~$0.025 |
| Cheap default LLM (Gemini Flash / GPT‑mini) | ~$0.02–0.05 |
| Twilio SIP | ~$0.004 |
| Orchestration / infra | ~$0.02–0.05 |
| **Total** | **~$0.06–0.12/min** |

**What we should charge.** Today's packs price a credit at **₹8 → ₹5** (`starter` → `scale`) ≈ **$0.096 → $0.059/min at ~₹85/$** — i.e. **at or below COGS**; `scale` is underwater for any premium model. The market retail band is **$0.15–0.35/min**, leaving room for a 2–3× markup. **Decision:** sell **one blended credit = 1 minute of base‑config talk‑time** (Bland/Telnyx model — hide the STT/TTS/LLM split), **pin the base rate to the cheapest stack**, and make premium models **burn more credits** via a multiplier (Retell/Synthflow). Re‑price toward **$0.15–0.20/min** (≈ ₹13–17/credit) **or** keep sticker prices and set `BILLING_MULTIPLIER > 1` so a connected minute burns >1 credit — that exact number is a founder decision (§10). Meter **to the second, no rounding** (fairness), with an optional **per‑call minimum** to cover dialing COGS.

---

## 5. Build‑vs‑Buy: OSS / 3rd‑Party vs In‑House

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Lago** (OSS metering+wallet, AGPLv3) | Purpose‑built prepaid wallet, events, plans | Separate **Rails service** + its own Postgres + sync layer; AGPL; another deploy unit; couples your hot‑path call gate to an external service's latency/availability | **No** — operational weight dwarfs the problem |
| **Stripe Billing / Metered** | Mature, hosted | USD‑first, no Razorpay parity for India; we'd still own reservation/idempotency on our side | **Not now** |
| **MPS (upstream Dograh remote ledger)** | Already in the codebase | Hosted‑only, USD‑ish, **separate unit**, reintroduces the two‑credits confusion; we don't control it | **Leave untouched** (OSS default already bypasses it) |
| **In‑house append‑only ledger on our own Postgres** | **No new infra**, sits next to the data it bills, reuses the race‑safe atomic UPDATE we already have, full control of correctness, Razorpay‑native | We own correctness (mitigated by the transactional design below) | ✅ **Build in‑house** |

**Call:** **Build in‑house.** The repo already has the single hardest primitive (an atomic conditional‑UPDATE debit). A billing system whose authoritative gate runs on every call‑start must live in the same Postgres/transaction boundary as the org row — an external Rails service (Lago) adds a network hop, a second source of truth to reconcile, and an extra failure domain on the money path, for capabilities we get from one table + one migration. The OSS research's own verdict agrees: *"In‑house append‑only ledger, Razorpay prepaid — no new infra — recommended."*

---

## 6. RECOMMENDED DESIGN — RMR‑2

> Synthesis rationale tied to scores: **Design 1 (7.6, fit 9)** is the backbone because it reuses the one good primitive with the smallest, zero‑downtime footprint. **Design 2 (correctness 9)** supplies the *transactional idempotency* that Design 1's judge said was missing ("the idempotency keys do not protect the balance mutation as written… double‑refund on reconcile retry"). **Design 3 (voiceFit 9)** supplies the margin/rating layer — but we adopt the judges' unanimous instruction to **drop per‑call escrow** (orphaned‑hold leak, double‑counting invariant) in favor of *reserve‑as‑real‑debit + balance‑bounded cap*.

### 6.1 Credit unit & margin math

- **Customer‑facing:** `1 credit = 1 minute = 60 seconds` of **connected** talk‑time (unchanged — preserves `CreditsSection.tsx` "1 credit = 1 minute").
- **Stored unit:** **SECONDS**, in the existing `organizations.free_call_seconds_remaining` (`models.py:158`). `NULL = unlimited`. **We keep seconds, not micro‑credits, for v1** — it preserves every existing atomic SQL path and the zero‑downtime migration; multipliers round to whole seconds. (Micro‑credits are a clean later upgrade *if* sub‑second per‑model pricing demands it — designed‑for, not built now.)
- **Billable seconds** = `max(BILLING_MINIMUM_SECONDS, round(call_duration_seconds × BILLING_MULTIPLIER × model_multiplier))`. All three multipliers **default to no‑op** (`1.0` / `0`) so the correctness fix ships without a pricing decision blocking it.
- **Margin levers (decouple credit from cents so we re‑tune without re‑quoting):** `BILLING_MULTIPLIER` (global), `BILLING_MINIMUM_SECONDS` (per‑call floor, Bland‑style), and a per‑model `CREDIT_RATE_MULTIPLIERS` map (cheap default = 1.0, Claude ~1.8, GPT‑5‑class ~2.5). Each is applied identically in **reserve** and **settle** math.

### 6.2 Ledger + reservation/idempotency model

**Keep the single mutable integer balance as the hot‑path source of truth** (DB‑side `GREATEST`/conditional‑UPDATE arithmetic already avoids read‑modify‑write races). **Add one append‑only `credit_ledger` table** that journals *every* mutation, and **no holds table** — the reservation **is a real debit** on the balance and the run row carries `reserved_seconds`.

**The reservation = an atomic up‑front debit.** Promote `try_charge_call_seconds` (`organization_client.py:95`) from number‑purchases to the per‑call mechanism. This single move makes **concurrency overrun and negative balance structurally impossible** with no new lock or queue (it serializes the debit on the org row).

**The load‑bearing correctness fix (Design 1 judge's mandated graft):** add **transactional variants** `reserve_call_seconds_tx(session, …)` and `settle_call_credits_tx(session, …)` that perform, in **ONE** `async with session.begin()`:
1. the conditional balance UPDATE (debit on reserve / refund on settle),
2. the `credit_ledger` row INSERT (with `UNIQUE idempotency_key`),
3. the `workflow_runs.reserved_seconds` / `credits_settled_at` flag UPDATE.

Because all three commit atomically, the `UNIQUE idempotency_key` now **guards the money, not just the audit row** — a retried ARQ job or a webhook/`/verify` race hits the unique constraint and the *entire* transaction (including the refund/credit) rolls back. This is what closes the double‑refund hole the judge identified.

**Idempotency keys:** `run:{id}:hold`, `run:{id}:settle`, `order:{razorpay_order_id}`. **Drift check:** `SUM(seconds_delta)` per org must equal `free_call_seconds_remaining` minus the opening grant → cheap nightly reconciliation.

### 6.3 Call‑lifecycle: meter → deduct → refund

```
                         ┌─────────────────────────────────────────────────────┐
 PRE-CALL GATE (fast)    │ assert_has_free_call_seconds → HTTP 402 if balance=0 │  (UX fast-fail, kept)
                         └─────────────────────────────────────────────────────┘
                                              │
 RESERVE (authoritative) │ after concurrency slot acquired (dispatcher ~548;        │
   = real up-front debit  │  public_agent; inbound-answer; single/test run):        │
                          │  hold = min(RESERVATION_CAP=300s, balance)             │
                          │  reserve_call_seconds_tx():  debit hold + 'hold' ledger │
                          │      row + set workflow_runs.reserved_seconds  (1 txn)  │
                          │  set this call's max_call_duration = hold  (HARD CAP)   │
                          │  → INSUFFICIENT ⇒ don't start, release slot, requeue    │
                                              │
 METER (unchanged)        │ pipeline_metrics_aggregator → usage_info.call_duration  │
                                              │
 SETTLE (idempotent)      │ replaces run_integrations.py Step 6c:                   │
   = refund remainder     │  guard on credits_settled_at (UNIQUE settle key)        │
                          │  billed   = billable(min(duration, reserved_seconds))   │
                          │  refund   = reserved_seconds - billed                   │
                          │  settle_call_credits_tx(): credit refund + 'settle'/    │
                          │      'refund' ledger rows + set credits_settled_at(1 txn)│
                                              │
 SWEEP (safety net)       │ ARQ cron: runs with reserved_seconds set, settled NULL, │
                          │  older than max_call_duration+grace ⇒ refund full hold  │
```

**Key inversion (why this is correct‑by‑construction):** because money leaves the balance **at reserve time**, a settle/DB hiccup now causes a *recoverable slight over‑charge* (the sweeper refunds it) — **never a silent free call**, which is the exact opposite of today's swallow‑and‑grant‑free behavior.

**Entrypoint coverage (Design 1 judge's second mandated graft):** the settle/reconcile path is **entrypoint‑agnostic**. `reserved_seconds = NULL` means *"meter post‑call idempotently"* (debit `billable(duration)`, no refund), **not** "use the old broken decrement." Only true pre‑deploy in‑flight runs (created before the migration timestamp) take a one‑time legacy fallback. Inbound calls are reserved at **answer** time; if an entrypoint can't reserve, it still gets an idempotent post‑call debit — **no run is ever left on the swallow path.**

### 6.4 Pricing & packs

- **Keep** the 3 INR packs (`constants.py:81`) and the derived plan tier (`plans.py`, highest paid pack → `api`/`mcp` feature gating) — they work end‑to‑end.
- **Keep** the bounded 30‑min trial grant (`DEFAULT_FREE_CALL_SECONDS=1800`).
- **Margin actions (founder decision, §10):** raise per‑credit toward $0.15–0.20/min **or** set `BILLING_MULTIPLIER > 1`; flag that `scale` (₹5/credit) is below COGS for premium models.
- **MRR levers designed‑for, deferred:** auto‑recharge (revive VAPP's `autoTopup` shape, *executed*), monetized concurrency (charge per extra line over a free baseline; we already have `OrganizationConfiguration` concurrency + a Redis Lua slot limiter), tiered fee → lower rate (Bland), number rental recurring fee (~8× Twilio).

### 6.5 Razorpay (and Stripe?)

- **Build entirely on the existing Razorpay flow** — `/verify` HMAC boundary (`razorpay_client.py:64‑75`) and server‑stored‑seconds crediting (`billing.py:126‑129`) are already correct; **reuse them.**
- **Close the webhook gap:** add `POST /api/v1/billing/webhook` — raw‑body `HMAC‑SHA256` against a **new** `RAZORPAY_WEBHOOK_SECRET` (constant‑time), handling `payment.captured`/`order.paid`, funneling through the **same idempotent credit path** as `/verify`.
- **Harden the paid transition (Design 3 judge's graft):** `mark_transaction_paid` is today a non‑atomic unconditional UPDATE. Convert to a compare‑and‑set — `UPDATE … SET status='paid' WHERE razorpay_order_id=:x AND status != 'paid'`, credit **only if rowcount > 0**, in **one txn** with the ledger `UNIQUE` insert (`order:{order_id}`). This makes webhook + `/verify` racing each other credit exactly once.
- **Stuck‑txn sweeper:** ARQ cron polls `payment_transactions` in `created` older than ~15 min, queries Razorpay, credits or fails.
- **Stripe: NO for v1.** Add a thin `PaymentProvider` seam (`create_order`/`verify`/`parse_webhook`) and a `provider` column **only when** real USD/international demand appears. The seam is clean; building it now doubles the money surface for zero benefit.

**Why this synthesis scores best:** it inherits Design 1's **fit‑9** (one table + two columns, reuse the existing primitive, additive nullable migration), repairs Design 1's two correctness holes with Design 2's **correctness‑9** transactional‑idempotency, borrows Design 3's **voiceFit‑9** margin levers as no‑op defaults, and explicitly **rejects** the heaviest, leakiest pieces all three judges flagged (separate holds table, micro‑credit cutover, mid‑call re‑reservation, Stripe).

---

## 7. DB / API / UI Changes (concrete to our files)

### 7.1 Database (one additive, zero‑downtime migration off head `754feb4556ce`)

**NEW table `credit_ledger`** (append‑only audit + idempotency):

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `organization_id` | FK→`organizations(id)`, indexed | |
| `entry_type` | `VARCHAR(24)` | `grant\|topup\|hold\|settle\|refund\|adjustment\|number_purchase` |
| `seconds_delta` | `INTEGER` signed | hold negative, refund positive |
| `balance_after` | `INTEGER` nullable | snapshot for drift detection |
| `workflow_run_id` | FK→`workflow_runs(id)` nullable, indexed | per‑run attribution |
| `razorpay_order_id` | `VARCHAR(64)` nullable | links top‑ups |
| `reason` | `TEXT` nullable | |
| `idempotency_key` | `VARCHAR(80)` nullable, **UNIQUE** | the money guard |
| `created_at` | `TIMESTAMPTZ` default now | + index `(organization_id, created_at)` |

**ALTER `workflow_runs`:** `reserved_seconds INTEGER NULL` (the hold; NULL ⇒ unmetered/pre‑deploy), `credits_settled_at TIMESTAMPTZ NULL` (settle idempotency guard).

**No change** to `organizations` (reuse `free_call_seconds_remaining`) or `payment_transactions` for v1 (add `provider VARCHAR` later with Stripe). Optional non‑blocking backfill: one `grant`/`adjustment` opening‑balance ledger row per currently‑metered org so `SUM(seconds_delta)` reconciles from day one.

**New env/constants:** `RAZORPAY_WEBHOOK_SECRET`; `RESERVATION_CAP_SECONDS` (default 300, reuse `run_pipeline.py:361`); `BILLING_MINIMUM_SECONDS` (default 0); `BILLING_MULTIPLIER` (default 1.0); optional `CREDIT_RATE_MULTIPLIERS` map.

### 7.2 API (thin routes → service per `api/AGENTS.md`)

- **NEW** `POST /api/v1/billing/webhook` — Razorpay raw‑body HMAC verify → shared idempotent credit path.
- **NEW** `GET /api/v1/billing/ledger` — org‑scoped paginated `credit_ledger` (the debit/refund history the system lacks).
- **MODIFY** `GET /api/v1/billing/balance` (`billing.py:45`) — also return `on_hold_seconds` (`SUM` of open holds: `reserved_seconds` set, `credits_settled_at NULL`).
- **NEW** service fns in `api/services/trial_credits.py`: `reserve_call_seconds(org, run_id, requested) → {UNLIMITED|RESERVED(n)|INSUFFICIENT}`, `settle_call_credits(run)`, `release_call_hold(run)` (sweeper) — wrapping the new transactional `*_tx` variants in `organization_client.py`.
- **NEW** transactional methods in `api/db/organization_client.py`: `reserve_call_seconds_tx(session,…)`, `settle_call_credits_tx(session,…)` (charge + ledger + flag in one `session.begin()`); a `topup_paid_tx(session,…)` doing the compare‑and‑set + ledger insert.
- **WIRE** `reserve_call_seconds` into all call‑start chokepoints: `campaign_call_dispatcher.py` (~548, after slot acquisition), `public_agent.py` (replacing bare assert at :344), inbound‑answer, single/test run start. Pass `max_call_duration = reserved` into the pipeline.
- **REPLACE** `run_integrations.py` Step 6c (`~333‑347`) with idempotent `settle_call_credits(run)`; delete the swallow‑and‑grant‑free behavior.
- **NEW** ARQ tasks: abandoned‑hold sweeper; stuck‑`created`‑txn poller; nightly drift reconciliation.
- **NEW** admin endpoint for manual refund/adjustment (writes `adjustment` ledger row + credit).
- Regenerate UI client (`npm run generate-client`) after route changes.

### 7.3 UI

- `ui/src/components/CreditsSection.tsx` (the live `/credits` page): show **available vs on‑hold** seconds, low‑balance banner (< ~5 min). Keep the Razorpay checkout untouched.
- **NEW** ledger/history panel driven by `GET /billing/ledger` — top‑ups (green), call settles (with run link), refunds, number purchases.
- **Hide the MPS surface in OSS mode** (`DEPLOYMENT_MODE == 'oss'`): `ui/src/components/billing/DograhCreditsCard.tsx` and the throwing `BuyCreditsControl.tsx` — so `/credits` is the single meaning of "credit." `PlanGuard.tsx` already redirects to `/credits`; no routing change.
- Copy: "1 credit = 1 minute, billed by connected duration to the second" (+ minimum/multiplier if enabled).

---

## 8. Edge Cases & How RMR‑2 Handles Them

| Edge case | Handling |
|---|---|
| **Concurrency overrun** | **Structurally impossible** — each call atomically *debits* its hold via `try_charge_call_seconds` before starting; only as many reservations succeed as the balance covers; the rest get `INSUFFICIENT` and requeue. No new lock. |
| **Mid‑call exhaustion** | The call's `max_call_duration` is **hard‑capped to `reserved_seconds`** (reuse existing run‑level plumbing); a call physically cannot outrun its hold. No mid‑call polling. |
| **Failed / no‑answer / busy** | `billed = billable(min(duration, reserved))`; a 0‑duration call refunds the **full** hold at settle. Optional `BILLING_MINIMUM_SECONDS` covers dialing COGS on connected calls. |
| **Double‑decrement on ARQ retry** | **Eliminated** — settle is one transaction guarded by `UNIQUE` settle key + `credits_settled_at`; a retry rolls back the whole txn (refund included), so it can't double‑refund either (the fix Design 1's judge demanded). |
| **Silent free‑call on DB error** | **Inverted** — money leaves at reserve; a settle failure = recoverable over‑charge refunded by the sweeper, never a free call. |
| **Negative balance** | **Impossible** — `try_charge` debits only when `balance ≥ hold`; we reserve `min(hold, balance)`. `GREATEST(.,0)` kept as belt‑and‑suspenders. |
| **Crash between reserve & completion** | Abandoned‑hold sweeper refunds holds for runs with `reserved_seconds` set, `credits_settled_at NULL`, older than `max_call_duration + grace`. Holds never leak. |
| **Top‑up webhook/`/verify` race** | Compare‑and‑set on `status` + `UNIQUE` ledger key `order:{id}` ⇒ credited exactly once. |
| **Captured payment, tab closed** | New webhook + stuck‑txn sweeper credit it. |
| **Unlimited (NULL) orgs** | `reserve` returns `UNLIMITED` (no hold); settle no‑ops; refund/credit paths **NULL‑guarded centrally** in the service — kills the `add_call_seconds` COALESCE footgun (`organization_client.py:71`). |
| **Inbound / non‑dispatcher runs** | Reserved at answer where possible; otherwise idempotent post‑call debit — **never** the legacy swallow path. |
| **Pre‑deploy in‑flight runs** | `reserved_seconds = NULL` + `created_at < deploy_ts` ⇒ one‑time legacy decrement, so no mis‑billing during cutover. |
| **Reconciliation / drift** | Nightly `SUM(seconds_delta)` per org vs `free_call_seconds_remaining`, alert on divergence. |
| **Refund / dispute** | Admin writes `adjustment` ledger row + credit; fully auditable per‑run. |

**Reservation‑size policy (explicitly decided per the judge):** `RESERVATION_CAP_SECONDS = 300` (the existing max) for v1. At the **default concurrency of 2**, an org needs only 600s to run 2 lines and the trial grants 1800s — so over‑reservation throttling is a **non‑issue at default settings**. It can only bite a low‑balance org that has *also* raised concurrency high; for that corner we ship a **per‑campaign cap override** now and defer **re‑reservation top‑up** (the complex part Design 3 hand‑waved) until call‑length data justifies a lower cap. UX note: low‑balance hard‑cap can shorten a live call; a configurable `GRACE_OVERDRAFT_SECONDS` (default 0, Bland‑style) is the friendly later mitigation.

---

## 9. Phased Implementation Plan (~6–8 engineer‑days)

Each phase ships independently and de‑risks the next. Honor the deploy memory's gates (alembic‑heads / submodule / import).

**Phase 0 — money‑leak fix (~0.5 day, no schema).** Razorpay webhook + compare‑and‑set `mark_transaction_paid` + stuck‑txn sweeper; hide the MPS `/billing` stub in OSS mode.
- *Tests:* webhook HMAC verify against Razorpay sample payloads; webhook + `/verify` race credits once; stuck‑txn poller credits a `created` order.

**Phase 1 — ledger table + columns (~1–1.5 days).** One additive nullable Alembic migration (`./scripts/makemigrate.sh`) off `754feb4556ce`: `credit_ledger` + `workflow_runs.reserved_seconds`/`credits_settled_at`. Make `/verify`, the webhook, and number‑purchase **also write ledger rows** (pure audit, no behavior change) — validates the table under real traffic. Add transactional `*_tx` methods.
- *Tests:* `UNIQUE idempotency_key` rejects dupes; ledger row written atomically with the balance mutation (rollback test); reconcile `SUM` matches balance.

**Phase 2 — core correctness (~2.5–3 days).** `reserve_call_seconds` wired into dispatcher/public/inbound/single‑run start (reuse `try_charge_call_seconds` + `max_call_duration` cap); replace `run_integrations.py:333‑347` with idempotent entrypoint‑agnostic `settle_call_credits`; abandoned‑hold sweeper; legacy fallback for pre‑deploy runs.
- *Tests:* concurrency — 5 calls, balance for 3 ⇒ exactly 3 reserve; retry of `process_workflow_completion` is a no‑op (no double‑deduct/refund); reconcile failure ⇒ over‑charge refunded by sweeper, never free; NULL/unlimited org never metered; inbound run still settles idempotently; pre‑deploy run uses legacy path once.

**Phase 3 — UI (~1 day).** Available vs on‑hold, ledger history panel, low‑balance banner. Regenerate client.

**Phase 4 — margin & MRR (later, non‑blocking).** Activate `BILLING_MULTIPLIER`/`MINIMUM`/per‑model multipliers; auto‑recharge; concurrency MRR; `PaymentProvider` seam for Stripe.

**Rollout on a live platform.** Ship **Phase 1 before Phase 2** so columns exist before the reserve logic reads them. Additive nullable columns = zero‑downtime; `reserved_seconds = NULL` legacy fallback prevents mis‑billing of in‑flight calls during cutover. Gate Phase 2's reserve behind a flag, watch the nightly drift check + a "reserved > 0 with no active call" alert, then remove the flag.

**Ship first:** Phase 0 (closes real money loss today) and Phase 2 (the correctness backbone).

---

## 10. Open Questions for the Founder

1. **Per‑credit price / margin %.** Keep ₹8–5/credit (≈ at/below COGS) or raise toward **$0.15–0.20/min (≈ ₹13–17/credit)** for a 60–75% gross margin? If keeping sticker prices, set `BILLING_MULTIPLIER` to what value (e.g. 1.5–2.0)? **`scale` at ₹5/credit is underwater for premium models — pick one lever.**
2. **Per‑model multipliers.** Ship the per‑model burn map in v1 (Gemini 1.0 / Claude ~1.8 / GPT‑5‑class ~2.5), or pin everyone to a cheap default first and add multipliers later?
3. **Per‑call minimum.** Enable `BILLING_MINIMUM_SECONDS` (e.g. 30s, Bland‑style) to cover dialing COGS on short calls — yes/no? It changes the bill for very short calls.
4. **Grace overdraft.** Allow a small negative dip (so a live call is never cut mid‑sentence) or hard‑stop at 0?
5. **Stripe.** Confirm **INR/Razorpay‑only for now**? (Recommended.) What's the trigger to add the `PaymentProvider` seam — first USD customer, or a date?
6. **Subscription vs pure prepaid.** Stay pure prepaid wallet + auto‑recharge, or add Bland‑style monthly tiers (higher fee → lower per‑credit rate) for MRR? Affects whether we build the subscription table in Phase 4.
7. **Concurrency monetization.** Free baseline (current default 2) + paid extra lines at ~₹700–850/line/mo with a 2× burst rate — in scope, or later?
8. **Reservation cap.** Confirm `RESERVATION_CAP = 300s` for v1 (safe at default concurrency); we add re‑reservation only if you plan to sell high concurrency to low‑balance/trial orgs.

---

**Key files this report grounded against (all verified in‑repo):** `api/db/organization_client.py:35,47,71,95` · `api/services/trial_credits.py:23,29,35,49` · `api/tasks/run_integrations.py:~333‑347` · `api/routes/billing.py:45,61,106,114,126,129` · `api/constants.py:44,68,72‑73,81,111,219` · `api/db/models.py:153,158,576,577,650,1379` · alembic head `754feb4556ce` (down_revision `91cc6ba3e1c7`,`e9c4a7b2f1d8`) over `c4a7b1e0f9d2`/`e9c4a7b2f1d8`/`2159d4ac431a`/`7feef09d7cc6`.