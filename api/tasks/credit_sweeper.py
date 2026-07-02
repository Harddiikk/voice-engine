"""Scheduled sweep that settles leaked credit reservation holds.

Every reservation (``workflow_runs.reserved_credit_seconds``) must be settled
exactly once (release the hold + charge the actual duration). The normal
settlement path is the post-call integrations task; the dial-failure path
settles inline. If both are missed (worker crash, lost callback, stuck run),
the org's balance silently keeps the hold deducted. This cron finds those
leaks and settles them via the same exactly-once service — the settlement CAS
makes racing with a late post-call task safe.

A hold counts as leaked when it is unsettled AND either:
- its run is completed and older than a 30-minute grace window (the post-call
  task should long since have settled it), or
- its run is older than 6 hours regardless of state (stuck INITIALIZED /
  RUNNING forever — no call lasts that long).
"""

from datetime import UTC, datetime, timedelta

from loguru import logger

from api.db import db_client
from api.services.credits.reservation import settle_workflow_run_credits

COMPLETED_GRACE = timedelta(minutes=30)
STUCK_RUN_AGE = timedelta(hours=6)


async def settle_leaked_credit_holds(ctx) -> dict:
    """Settle every leaked reservation hold (origin='sweeper' → kind=leak_sweep)."""
    now = datetime.now(UTC)
    holds = await db_client.list_unsettled_credit_holds(
        completed_cutoff=now - COMPLETED_GRACE,
        stale_cutoff=now - STUCK_RUN_AGE,
    )
    if not holds:
        return {"leaked": 0, "settled": 0}

    settled = 0
    for workflow_run, organization_id in holds:
        try:
            outcome = await settle_workflow_run_credits(
                organization_id, workflow_run, origin="sweeper"
            )
            if outcome == "settled":
                settled += 1
        except Exception as exc:
            logger.warning(
                f"Credit sweeper failed to settle run "
                f"{getattr(workflow_run, 'id', '?')}: {exc}"
            )

    logger.info(
        f"Credit sweeper: {len(holds)} leaked hold(s) found, {settled} settled"
    )
    return {"leaked": len(holds), "settled": settled}
