# Credits Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local `free_call_seconds_remaining` ledger the single billing system — kill the dormant Dograh MPS path behind a default-off flag, and gate + charge every runtime entry point exactly once via race-safe reservation + reconcile.

**Architecture:** All runtime entry points already call `authorize_workflow_run_start`. We fold a local credit reservation into that one function (so no entry-point edits are needed for gating), neutralize the MPS pre-call gate and post-call report behind a new `MANAGED_MODEL_SERVICES_ENABLED` flag, and replace the unconditional post-call `consume_free_call_seconds` with a `reconcile` that releases the reservation hold then charges the true call duration. Reconcile degrades to today's behavior when no reservation exists, so it is safe everywhere.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy (async), pytest (asyncio auto-mode), Postgres.

## Global Constraints

- `1 credit = 60 seconds`. Balance lives in `organizations.free_call_seconds_remaining` (Integer, nullable).
- `NULL` balance = **unlimited/unmetered**: never gate it, never charge it, never convert it to a number (top-ups/refunds must skip `NULL` orgs).
- User-facing strings must be **de-branded** — no "Dograh", no `founders@dograh.com`.
- Tests run against the test DB: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest <path> -v`
- Async tests use auto-mode: plain `async def test_*`, no `@pytest.mark.asyncio` (mirror `api/tests/test_trial_credits.py`).
- Mock DB by patching the module's `db_client`: `patch.object(<module>.db_client, "<method>", new=AsyncMock(return_value=...))`.
- The MPS enum value `"dograh"` / `ServiceProviders.DOGRAH`, auth cookies, and localStorage keys are wire/DB contracts — do **not** rename them.

## Pre-Implementation Safety Check (run once before enabling the flag in prod)

The flag defaults **off**, so merging this is safe regardless. But before flipping `MANAGED_MODEL_SERVICES_ENABLED` on (or relying on the kill in prod), confirm no workflow uses Dograh-managed models (killing MPS also removes the correlation-id mint those need for model *access*):

```sql
-- expect 0 rows
SELECT id, organization_id FROM workflows
WHERE workflow_configurations::text LIKE '%"mode":"dograh"%' LIMIT 20;
```
If rows exist: migrate them to `byok` first, or keep MPS as an either/or switch for those orgs.

---

### Task 1: Credit reservation/reconcile service (+ constants)

**Files:**
- Modify: `api/constants.py` (add two constants after `NUMBER_SETUP_MINUTES`, ~line 111)
- Create: `api/services/credits/__init__.py`
- Create: `api/services/credits/reservation.py`
- Test: `api/tests/test_credit_reservation.py`

**Interfaces:**
- Produces:
  - `RESERVED_CREDIT_SECONDS_KEY: str = "reserved_credit_seconds"`
  - `INSUFFICIENT_CREDITS_MESSAGE: str`
  - `async reserve_call_credits(organization_id: int, est_seconds: int) -> int | None` — returns reserved seconds (`0` if unmetered/free) on success, `None` if the metered balance can't cover `est_seconds`.
  - `async reconcile_call_credits(organization_id: int, reserved_seconds: int, actual_seconds: float | int | None) -> None` — releases the hold (refunds `reserved_seconds`) then charges the true `actual_seconds`; best-effort, never raises.
  - `async settle_workflow_run_credits(organization_id: int, workflow_run) -> None` — reads `reserved`/`duration` off a completed run and calls `reconcile_call_credits`.
- Consumes: `db_client.get_free_call_seconds_remaining`, `db_client.try_charge_call_seconds`, `db_client.add_call_seconds` (all in `api/db/organization_client.py`); `consume_free_call_seconds` from `api/services/trial_credits.py`.

- [ ] **Step 1: Add the constants**

In `api/constants.py`, immediately after the `NUMBER_SETUP_MINUTES = ...` line (~111):

```python
# Single-ledger billing. When False (default) the upstream Dograh MPS model
# billing is OFF and the local call-minute credit ledger
# (organizations.free_call_seconds_remaining) is the ONLY billing system.
MANAGED_MODEL_SERVICES_ENABLED = (
    os.getenv("MANAGED_MODEL_SERVICES_ENABLED", "false").lower() == "true"
)
# Seconds held per in-flight call as a race-safe reservation, then reconciled to
# the call's true duration on completion. ~ a generous maximum call length.
CREDIT_RESERVATION_SECONDS = int(os.getenv("CREDIT_RESERVATION_SECONDS", "600"))
```

- [ ] **Step 2: Create the package init**

Create `api/services/credits/__init__.py` (empty file).

- [ ] **Step 3: Write the failing test**

Create `api/tests/test_credit_reservation.py`:

```python
"""Credit reservation + reconcile: reserve, insufficient, unmetered, settle."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.services.credits import reservation
from api.services.credits.reservation import (
    RESERVED_CREDIT_SECONDS_KEY,
    reconcile_call_credits,
    reserve_call_credits,
    settle_workflow_run_credits,
)


def _patch(method, **kw):
    return patch.object(reservation.db_client, method, new=AsyncMock(**kw))


async def test_reserve_unmetered_returns_zero_and_never_charges():
    charge = AsyncMock(return_value=True)
    with _patch("get_free_call_seconds_remaining", return_value=None), patch.object(
        reservation.db_client, "try_charge_call_seconds", new=charge
    ):
        assert await reserve_call_credits(1, 600) == 0
    charge.assert_not_awaited()


async def test_reserve_metered_sufficient_returns_est():
    with _patch("get_free_call_seconds_remaining", return_value=1000), _patch(
        "try_charge_call_seconds", return_value=True
    ):
        assert await reserve_call_credits(1, 600) == 600


async def test_reserve_metered_insufficient_returns_none():
    with _patch("get_free_call_seconds_remaining", return_value=100), _patch(
        "try_charge_call_seconds", return_value=False
    ):
        assert await reserve_call_credits(1, 600) is None


async def test_reconcile_metered_releases_hold_then_charges_actual():
    add = AsyncMock(return_value=470)
    consume = AsyncMock()
    with _patch("get_free_call_seconds_remaining", return_value=400), patch.object(
        reservation.db_client, "add_call_seconds", new=add
    ), patch.object(reservation, "consume_free_call_seconds", new=consume):
        await reconcile_call_credits(1, 600, 130)
    add.assert_awaited_once_with(1, 600)
    consume.assert_awaited_once_with(1, 130)


async def test_reconcile_no_reservation_only_consumes():
    add = AsyncMock()
    consume = AsyncMock()
    with patch.object(reservation.db_client, "add_call_seconds", new=add), patch.object(
        reservation, "consume_free_call_seconds", new=consume
    ):
        await reconcile_call_credits(1, 0, 95)
    add.assert_not_awaited()
    consume.assert_awaited_once_with(1, 95)


async def test_reconcile_swallows_errors():
    with patch.object(
        reservation, "consume_free_call_seconds", new=AsyncMock(side_effect=RuntimeError("x"))
    ):
        await reconcile_call_credits(1, 0, 10)  # must not raise


async def test_settle_reads_reserved_and_duration_off_run():
    run = SimpleNamespace(
        initial_context={RESERVED_CREDIT_SECONDS_KEY: 600},
        usage_info={"call_duration_seconds": 130},
        cost_info={},
    )
    rec = AsyncMock()
    with patch.object(reservation, "reconcile_call_credits", new=rec):
        await settle_workflow_run_credits(1, run)
    rec.assert_awaited_once_with(1, 600, 130)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_credit_reservation.py -v`
Expected: FAIL — `ModuleNotFoundError: api.services.credits.reservation`.

- [ ] **Step 5: Write the implementation**

Create `api/services/credits/reservation.py`:

```python
"""Race-safe call-credit reservation + reconcile (single local ledger).

reserve a fixed hold before a run (atomic, so concurrent calls can't oversell),
then reconcile on completion: release the hold and charge the true duration so
the net deduction equals the call's actual length. Reconcile degrades to a plain
post-call charge when no reservation was taken, so it is safe at every entry point.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.services.trial_credits import consume_free_call_seconds

RESERVED_CREDIT_SECONDS_KEY = "reserved_credit_seconds"

INSUFFICIENT_CREDITS_MESSAGE = (
    "You're out of calling credits. Add credits from Billing to keep making calls."
)


async def reserve_call_credits(organization_id: int, est_seconds: int) -> int | None:
    """Reserve `est_seconds` of credits for an in-flight call.

    Returns the reserved seconds (0 when the org is unmetered/unlimited, i.e. no
    charge) on success, or None when the metered balance cannot cover the estimate.
    """
    balance = await db_client.get_free_call_seconds_remaining(organization_id)
    if balance is None:
        return 0  # unmetered / unlimited — allowed, nothing reserved
    if est_seconds <= 0:
        return 0
    if await db_client.try_charge_call_seconds(organization_id, est_seconds):
        return est_seconds
    return None


async def reconcile_call_credits(
    organization_id: int, reserved_seconds: int, actual_seconds: float | int | None
) -> None:
    """Release the reservation hold, then charge the true call duration.

    Net deduction == actual usage. No-op for unmetered orgs (consume skips NULL).
    Best-effort: a ledger hiccup must never break post-call processing.
    """
    try:
        if reserved_seconds and reserved_seconds > 0:
            balance = await db_client.get_free_call_seconds_remaining(organization_id)
            if balance is not None:  # never convert an unmetered org to metered
                await db_client.add_call_seconds(organization_id, int(reserved_seconds))
        await consume_free_call_seconds(organization_id, actual_seconds)
    except Exception as exc:
        logger.warning(f"Credit reconcile failed for org {organization_id}: {exc}")


async def settle_workflow_run_credits(organization_id: int, workflow_run) -> None:
    """Reconcile credits for a completed run from its reserved hold + duration."""
    ctx = getattr(workflow_run, "initial_context", None) or {}
    reserved = ctx.get(RESERVED_CREDIT_SECONDS_KEY) or 0
    usage = getattr(workflow_run, "usage_info", None) or {}
    cost = getattr(workflow_run, "cost_info", None) or {}
    duration = usage.get("call_duration_seconds") or cost.get("call_duration_seconds")
    await reconcile_call_credits(organization_id, reserved, duration)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_credit_reservation.py -v`
Expected: PASS (7 passed).

- [ ] **Step 7: Commit**

```bash
git add api/constants.py api/services/credits/__init__.py api/services/credits/reservation.py api/tests/test_credit_reservation.py
git commit -m "feat(billing): credit reservation + reconcile service"
```

---

### Task 2: Make the local ledger the single pre-call gate

**Files:**
- Modify: `api/services/quota_service.py` (imports; add local branch + 2 helpers in `authorize_workflow_run_start`)
- Test: `api/tests/test_quota_service.py` (append new tests)

**Interfaces:**
- Consumes: `reserve_call_credits`, `RESERVED_CREDIT_SECONDS_KEY`, `INSUFFICIENT_CREDITS_MESSAGE` (Task 1); `has_free_call_seconds` (`api/services/trial_credits.py`); `MANAGED_MODEL_SERVICES_ENABLED`, `CREDIT_RESERVATION_SECONDS` (Task 1 constants).
- Produces: `authorize_workflow_run_start` behavior — when MPS disabled, an actual run (with `workflow_run_id`) reserves credits and stores the hold on the run; a pre-flight (no `workflow_run_id`) checks balance only; MPS code never runs.

- [ ] **Step 1: Add imports**

In `api/services/quota_service.py`, extend the constants import and add the new ones near the top imports:

```python
from api.constants import (
    CREDIT_RESERVATION_SECONDS,
    DEPLOYMENT_MODE,
    MANAGED_MODEL_SERVICES_ENABLED,
)
from api.services.credits.reservation import (
    INSUFFICIENT_CREDITS_MESSAGE,
    RESERVED_CREDIT_SECONDS_KEY,
    reserve_call_credits,
)
from api.services.trial_credits import has_free_call_seconds
```
(The existing `from api.constants import DEPLOYMENT_MODE` line is replaced by the grouped import above.)

- [ ] **Step 2: Add the helper functions**

Add near the other module-level helpers (e.g. after `_insufficient_legacy_quota_result`):

```python
def _insufficient_credits_result() -> QuotaCheckResult:
    return QuotaCheckResult(
        has_quota=False,
        error_code="insufficient_credits",
        error_message=INSUFFICIENT_CREDITS_MESSAGE,
    )


async def _store_reserved_credit_seconds(
    workflow_run_id: int | None, seconds: int
) -> None:
    """Persist the reserved hold on the run so completion can reconcile it."""
    if not workflow_run_id or not seconds:
        return
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        return
    initial_context = dict(workflow_run.initial_context or {})
    initial_context[RESERVED_CREDIT_SECONDS_KEY] = seconds
    await db_client.update_workflow_run(
        workflow_run_id, initial_context=initial_context
    )
```

- [ ] **Step 3: Write the failing tests**

Append to `api/tests/test_quota_service.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.services import quota_service


def _workflow(org_id=1, user_id=2, wf_id=10):
    return SimpleNamespace(id=wf_id, organization_id=org_id, user_id=user_id)


async def test_run_reserves_and_passes_when_mps_disabled():
    with patch.object(quota_service.db_client, "get_workflow_by_id",
                      new=AsyncMock(return_value=_workflow())), \
         patch.object(quota_service, "reserve_call_credits",
                      new=AsyncMock(return_value=600)) as reserve, \
         patch.object(quota_service, "_store_reserved_credit_seconds",
                      new=AsyncMock()) as store:
        result = await quota_service.authorize_workflow_run_start(
            workflow_id=10, workflow_run_id=5
        )
    assert result.has_quota is True
    reserve.assert_awaited_once_with(1, quota_service.CREDIT_RESERVATION_SECONDS)
    store.assert_awaited_once_with(5, 600)


async def test_run_insufficient_credits_returns_402_code():
    with patch.object(quota_service.db_client, "get_workflow_by_id",
                      new=AsyncMock(return_value=_workflow())), \
         patch.object(quota_service, "reserve_call_credits",
                      new=AsyncMock(return_value=None)):
        result = await quota_service.authorize_workflow_run_start(
            workflow_id=10, workflow_run_id=5
        )
    assert result.has_quota is False
    assert result.error_code == "insufficient_credits"


async def test_preflight_blocks_on_zero_balance():
    with patch.object(quota_service.db_client, "get_workflow_by_id",
                      new=AsyncMock(return_value=_workflow())), \
         patch.object(quota_service, "has_free_call_seconds",
                      new=AsyncMock(return_value=False)):
        result = await quota_service.authorize_workflow_run_start(workflow_id=10)
    assert result.has_quota is False
    assert result.error_code == "insufficient_credits"


async def test_preflight_allows_positive_balance():
    with patch.object(quota_service.db_client, "get_workflow_by_id",
                      new=AsyncMock(return_value=_workflow())), \
         patch.object(quota_service, "has_free_call_seconds",
                      new=AsyncMock(return_value=True)):
        result = await quota_service.authorize_workflow_run_start(workflow_id=10)
    assert result.has_quota is True


async def test_mps_client_not_called_when_disabled():
    calls = AsyncMock()
    with patch.object(quota_service.db_client, "get_workflow_by_id",
                      new=AsyncMock(return_value=_workflow())), \
         patch.object(quota_service, "reserve_call_credits",
                      new=AsyncMock(return_value=0)), \
         patch.object(quota_service, "_store_reserved_credit_seconds", new=AsyncMock()), \
         patch.object(quota_service.mps_service_key_client,
                      "authorize_workflow_run_start", new=calls):
        await quota_service.authorize_workflow_run_start(workflow_id=10, workflow_run_id=5)
    calls.assert_not_awaited()
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_quota_service.py -v -k "reserves or insufficient or preflight or mps_client_not"`
Expected: FAIL — the local branch doesn't exist yet (reserve not called / MPS path runs).

- [ ] **Step 5: Add the local-billing branch**

In `authorize_workflow_run_start`, insert this block immediately **after** the actor-org validation and **before** `workflow_owner = await db_client.get_user_by_id(...)`:

```python
        # Single-ledger billing: when MPS is disabled (default), the local
        # call-minute credit ledger is the only gate. An actual run reserves a
        # race-safe hold (reconciled to true duration on completion); a pre-flight
        # check (no run id, e.g. campaign start) just verifies a positive balance.
        if not MANAGED_MODEL_SERVICES_ENABLED:
            org_id = workflow.organization_id
            if workflow_run_id is None:
                if not await has_free_call_seconds(org_id):
                    return _insufficient_credits_result()
                return QuotaCheckResult(has_quota=True)
            reserved = await reserve_call_credits(org_id, CREDIT_RESERVATION_SECONDS)
            if reserved is None:
                return _insufficient_credits_result()
            await _store_reserved_credit_seconds(workflow_run_id, reserved)
            return QuotaCheckResult(has_quota=True)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_quota_service.py -v`
Expected: PASS (existing + 5 new). If a pre-existing test assumed MPS ran with the flag unset, update it to set `quota_service.MANAGED_MODEL_SERVICES_ENABLED = True` via patch for that case.

- [ ] **Step 7: Commit**

```bash
git add api/services/quota_service.py api/tests/test_quota_service.py
git commit -m "feat(billing): reserve local credits in authorize_workflow_run_start when MPS off"
```

---

### Task 3: Neutralize the MPS post-call usage report

**Files:**
- Modify: `api/services/workflow_run_billing.py` (import flag; early-return)
- Test: `api/tests/test_workflow_run_billing.py` (append a test)

**Interfaces:**
- Consumes: `MANAGED_MODEL_SERVICES_ENABLED` (Task 1).
- Produces: `report_workflow_run_platform_usage` no-ops when MPS disabled (even if `DEPLOYMENT_MODE != "oss"`), so the local reconcile is the only post-call charge.

- [ ] **Step 1: Write the failing test**

Append to `api/tests/test_workflow_run_billing.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.services import workflow_run_billing


async def test_report_noop_when_mps_disabled_even_if_hosted():
    run = SimpleNamespace(id=1, is_completed=True)
    report = AsyncMock()
    with patch.object(workflow_run_billing, "DEPLOYMENT_MODE", "hosted"), \
         patch.object(workflow_run_billing, "MANAGED_MODEL_SERVICES_ENABLED", False), \
         patch.object(workflow_run_billing.mps_service_key_client,
                      "report_platform_usage", new=report):
        await workflow_run_billing.report_workflow_run_platform_usage(run)
    report.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_workflow_run_billing.py::test_report_noop_when_mps_disabled_even_if_hosted -v`
Expected: FAIL — currently proceeds past the `oss` check when `DEPLOYMENT_MODE="hosted"`.

- [ ] **Step 3: Add the flag import and guard**

In `api/services/workflow_run_billing.py`, extend the constants import:

```python
from api.constants import DEPLOYMENT_MODE, MANAGED_MODEL_SERVICES_ENABLED
```

Then in `report_workflow_run_platform_usage`, immediately after the existing `if DEPLOYMENT_MODE == "oss": return` block, add:

```python
    if not MANAGED_MODEL_SERVICES_ENABLED:
        return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_workflow_run_billing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/workflow_run_billing.py api/tests/test_workflow_run_billing.py
git commit -m "feat(billing): skip MPS platform-usage report when MPS disabled"
```

---

### Task 4: Make reconcile the single post-call charge

**Files:**
- Modify: `api/tasks/run_integrations.py:333-347` (replace the `consume_free_call_seconds` block with `settle_workflow_run_credits`)

**Interfaces:**
- Consumes: `settle_workflow_run_credits` (Task 1). `organization_id` and `workflow_run` are already in scope at this point in `run_integrations_post_workflow_run`.

- [ ] **Step 1: Replace the post-call charge block**

In `api/tasks/run_integrations.py`, replace the existing "Step 6c" block (currently importing and calling `consume_free_call_seconds`) with:

```python
        # Step 6c: settle the org's call-minute ledger for this run — release the
        # reservation hold (if any) and charge the true duration. Single charge;
        # no-op for unmetered orgs. Best-effort.
        try:
            from api.services.credits.reservation import settle_workflow_run_credits

            await settle_workflow_run_credits(organization_id, workflow_run)
        except Exception as exc:
            logger.warning(
                f"Credit settle failed for run {workflow_run_id}: {exc}"
            )
```

- [ ] **Step 2: Verify the old direct consume call is gone**

Run: `grep -n "consume_free_call_seconds" api/tasks/run_integrations.py`
Expected: no matches (the only caller is now inside `reconcile_call_credits`).

- [ ] **Step 3: Run the billing-related suites to confirm green**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_credit_reservation.py api/tests/test_trial_credits.py api/tests/test_workflow_run_billing.py api/tests/test_workflow_run_billing.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add api/tasks/run_integrations.py
git commit -m "feat(billing): single post-call charge via credit reconcile"
```

---

### Task 5: Remove the now-redundant double-gates

The local balance check now lives inside `authorize_workflow_run_start` (pre-flight branch), so the explicit `assert_has_free_call_seconds` calls at campaign start/resume and the public trigger are redundant double-gates and must be removed (they would block with a different, branded 402 message).

**Files:**
- Modify: `api/routes/campaign.py:557` (remove `assert_has_free_call_seconds`)
- Modify: `api/routes/campaign.py` (resume, ~line 890 — remove `assert_has_free_call_seconds`)
- Modify: `api/routes/public_agent.py:344` (remove `assert_has_free_call_seconds`)

- [ ] **Step 1: Remove the campaign-start assert**

In `api/routes/campaign.py`, in `start_campaign`, delete the line:

```python
    await assert_has_free_call_seconds(campaign.organization_id)
```
Keep the `await assert_org_kyc_complete(...)` line above it and the `authorize_workflow_run_start(...)` block below it (the latter now performs the balance check).

- [ ] **Step 2: Remove the campaign-resume assert**

In `api/routes/campaign.py`, in `resume_campaign` (~line 890), delete the same `await assert_has_free_call_seconds(campaign.organization_id)` line, keeping KYC + the `authorize_workflow_run_start` block.

- [ ] **Step 3: Remove the public-trigger assert**

In `api/routes/public_agent.py`, in `_initiate_call` (~line 344), delete:

```python
    await assert_has_free_call_seconds(api_key.organization_id)
```
Keep the `await assert_org_kyc_complete(api_key.organization_id)` line. (The run-level `authorize_workflow_run_start(workflow_run_id=...)` in `_execute_resolved_target` now reserves + gates.)

- [ ] **Step 4: Clean up now-unused imports**

For each file, if `assert_has_free_call_seconds` is no longer referenced, remove it from the `from api.services.trial_credits import ...` line. Verify:

Run: `grep -n "assert_has_free_call_seconds" api/routes/campaign.py api/routes/public_agent.py`
Expected: no matches.

- [ ] **Step 5: Run route + dispatcher suites**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_public_agent_routes.py -v`
Expected: PASS. If a test asserted the pre-flight branded 402 message/path, update it to expect the unified `insufficient_credits` gate (now enforced via `authorize_workflow_run_start`).

- [ ] **Step 6: Commit**

```bash
git add api/routes/campaign.py api/routes/public_agent.py
git commit -m "refactor(billing): drop redundant local credit double-gates"
```

---

### Task 6: Document env flags + full-suite verification

**Files:**
- Modify: `api/.env.example` (if present — add the two flags with safe defaults; if absent, create a one-line note in `docs/contribution/setup.mdx` billing section)

- [ ] **Step 1: Document the flags**

Add to `api/.env.example` (create the lines if the file exists; otherwise skip and note in setup docs):

```bash
# Single-ledger billing (default off = local call-minute credits are the only billing)
MANAGED_MODEL_SERVICES_ENABLED=false
DEPLOYMENT_MODE=oss
CREDIT_RESERVATION_SECONDS=600
```

- [ ] **Step 2: Run the full billing-affected test set**

Run:
```bash
source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest \
  api/tests/test_credit_reservation.py \
  api/tests/test_quota_service.py \
  api/tests/test_trial_credits.py \
  api/tests/test_workflow_run_billing.py \
  api/tests/test_workflow_run_billing.py \
  api/tests/test_public_agent_routes.py \
  api/tests/test_organization_usage_billing.py -v
```
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add api/.env.example
git commit -m "docs(billing): document single-ledger billing env flags"
```

---

## Out of Scope / Follow-up (separate specs + plans)

These were identified during inspection but are intentionally **not** in this plan:

- **Hide the `mode="dograh"` option in the model-config UI picker** (`AIModelConfigurationV2Editor`) so no new MPS-dependent config can be created after the kill. Superuser-only surface; low risk. Small UI follow-up.
- **Phone-number purchase deduction** — set a real `NUMBER_SETUP_MINUTES`, surface the price in `PhoneNumbersSection`, decide BYO/recurring (its own spec).
- **Agent-builder token metering** — capture `message.usage`, add pre-check + rate-limit + idempotency, charge per generation (its own spec).
- **Per-campaign budget** — new `budget_seconds` column + race-safe consumed ledger (its own spec).
- **De-Dograh branding** + **Airtable theme** (parallel UI track).
- **Optional hardening:** a reservation-sweep job that refunds holds for runs that died before reconcile (TTL-based). Not required because reconcile runs in the standard completion pipeline; add only if orphaned-run leakage is observed.

## Self-Review

**Spec coverage:** §4A kill-switch → Tasks 2+3+6; §4B/E single gate at all entry points → Task 2 (folded into `authorize_workflow_run_start`, which every entry point already calls); §4C atomic reservation → Task 1+2; §4D one post-call charge → Tasks 1+4; §4F text-chat/WebRTC metering → covered for free (all session types already emit `call_duration_seconds`); §4G shared wallet / NULL sentinel → Task 1 (`reserve`/`reconcile` skip NULL); §6 precondition → Pre-Implementation Safety Check; §8 test plan → Tasks 1–6 tests. Double-gate collapse → Task 5.

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every test shows real assertions.

**Type consistency:** `reserve_call_credits -> int | None`, `RESERVED_CREDIT_SECONDS_KEY`, `INSUFFICIENT_CREDITS_MESSAGE`, `settle_workflow_run_credits(organization_id, workflow_run)` used identically across Tasks 1, 2, 4. `_insufficient_credits_result()` / `_store_reserved_credit_seconds()` defined and used in Task 2 only. Constants `MANAGED_MODEL_SERVICES_ENABLED`, `CREDIT_RESERVATION_SECONDS` defined in Task 1, consumed in Tasks 2/3.
