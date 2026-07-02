"""Race-safe call-credit reservation + settlement (single local ledger).

Reserve a fixed hold before a run (atomic, so concurrent calls can't
oversell), then settle on completion: release the hold and charge the true
duration so the net deduction equals the call's actual length. Both sides are
single database transactions (see ``api/db/credit_ledger_client.py``) that
also write the append-only ``credit_ledger`` audit rows:

- reserve: conditional debit + ledger row + ``workflow_runs.
  reserved_credit_seconds`` commit together — no crash window between "money
  held" and "hold recorded on the run".
- settle: a CAS on ``workflow_runs.credits_settled_at`` makes settlement
  exactly-once even under concurrent retries (the old JSON
  ``credits_settled`` read-then-write flag was racy).

Unmetered orgs (NULL balance) are never charged and produce no ledger rows.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.db.credit_ledger_client import ALREADY_APPLIED
from api.services.trial_credits import consume_free_call_seconds

RESERVED_CREDIT_SECONDS_KEY = "reserved_credit_seconds"
# Legacy JSON settlement flag (pre-ledger runs). Still honored as a settle
# pre-guard so historical runs that were settled via initial_context are never
# re-settled; new settlements mark ``workflow_runs.credits_settled_at``.
CREDITS_SETTLED_KEY = "credits_settled"

INSUFFICIENT_CREDITS_MESSAGE = (
    "You're out of calling credits. Add credits from Billing to keep making calls."
)


async def reserve_call_credits_for_run(
    organization_id: int, workflow_run_id: int, est_seconds: int
) -> int | None:
    """Reserve ``est_seconds`` of credits for a specific in-flight run.

    Returns the reserved seconds on success (0 when the org is unmetered /
    nothing to hold — allowed, free), or None when the metered balance cannot
    cover the estimate. The hold, its ledger row and the run's
    ``reserved_credit_seconds`` are written in one transaction; a retried
    authorization for the same run is idempotent (treated as reserved).
    """
    balance = await db_client.get_free_call_seconds_remaining(organization_id)
    if balance is None:
        return 0  # unmetered / unlimited — allowed, nothing reserved
    if est_seconds <= 0:
        return 0
    result = await db_client.reserve_run_credits_tx(
        organization_id, workflow_run_id, est_seconds
    )
    if result == ALREADY_APPLIED:
        return est_seconds  # retried authorization — the hold already exists
    return result


async def reserve_call_credits(organization_id: int, est_seconds: int) -> int | None:
    """Legacy run-less reservation (no ledger row target). Prefer
    :func:`reserve_call_credits_for_run` — kept for backward compatibility.
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
    """Legacy release-then-charge (not ledgered, not exactly-once). Prefer
    :func:`settle_workflow_run_credits` — kept for backward compatibility.
    """
    try:
        if reserved_seconds and reserved_seconds > 0:
            balance = await db_client.get_free_call_seconds_remaining(organization_id)
            if balance is not None:  # never convert an unmetered org to metered
                await db_client.add_call_seconds(organization_id, int(reserved_seconds))
        await consume_free_call_seconds(organization_id, actual_seconds)
    except Exception as exc:
        logger.warning(f"Credit reconcile failed for org {organization_id}: {exc}")


def _run_duration_seconds(workflow_run) -> float | int:
    usage = getattr(workflow_run, "usage_info", None) or {}
    cost = getattr(workflow_run, "cost_info", None) or {}
    return (
        usage.get("call_duration_seconds")
        or cost.get("call_duration_seconds")
        or 0
    )


def _settle_description(workflow_run, duration) -> str:
    ctx = getattr(workflow_run, "initial_context", None) or {}
    try:
        total = int(round(float(duration or 0)))
    except (TypeError, ValueError):
        total = 0
    minutes, seconds = divmod(max(0, total), 60)
    return f"Call to {ctx.get('called_number', '?')} — {minutes}m {seconds:02d}s"


async def settle_workflow_run_credits(
    organization_id: int, workflow_run, origin: str = "settle"
) -> str:
    """Settle credits for a completed run: release its hold, charge duration.

    Exactly-once: the underlying transaction CASes
    ``workflow_runs.credits_settled_at`` first, so a retried post-call task, a
    concurrent sweeper pass and an inline dial-failure settle can never
    double-release or double-charge. Legacy runs that were settled via the old
    ``initial_context.credits_settled`` JSON flag are recognized and skipped.

    Returns 'settled' or 'already' (already settled / nothing to do).
    """
    ctx = getattr(workflow_run, "initial_context", None) or {}
    if ctx.get(CREDITS_SETTLED_KEY):
        return ALREADY_APPLIED  # legacy JSON-era settlement — never re-settle

    run_id = getattr(workflow_run, "id", None)
    if run_id is None:
        return ALREADY_APPLIED

    reserved = getattr(workflow_run, "reserved_credit_seconds", None)
    if reserved is None:
        # JSON-era hold (pre-migration runs) — fall back to initial_context.
        reserved = ctx.get(RESERVED_CREDIT_SECONDS_KEY) or 0

    duration = _run_duration_seconds(workflow_run)
    return await db_client.settle_run_credits_tx(
        organization_id,
        run_id,
        int(reserved or 0),
        duration,
        origin=origin,
        description=_settle_description(workflow_run, duration),
    )
