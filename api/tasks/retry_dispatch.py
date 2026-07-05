"""Retry dispatch-cron — a resilience backstop for due campaign retries.

The campaign orchestrator's in-process loop normally picks up due retries within
~60s. If that loop isn't running (worker crash/restart), due retries would sit
unclaimed. This cron independently enqueues a batch for each RUNNING campaign
that has due retries AND is currently within its calling window.

Safety:
- No double-dispatch vs the orchestrator: batch claiming uses
  ``FOR UPDATE SKIP LOCKED``, so a cron batch and an orchestrator batch never
  claim the same queued row (at worst an extra empty batch job).
- No off-window dialing: we check ``is_within_schedule`` before enqueuing (the
  batch task itself does not re-check the window).
"""

from datetime import UTC, datetime

from loguru import logger

from api.db import db_client
from api.services.campaign.schedule import is_within_schedule
from api.tasks.arq_pool import enqueue_job
from api.tasks.function_names import FunctionNames

_BATCH_SIZE = 10


async def dispatch_due_campaign_retries(ctx) -> dict:
    """Enqueue a batch for each running campaign with due, in-window retries."""
    now = datetime.now(UTC)
    campaigns = await db_client.list_running_campaigns_with_due_retries(now)
    enqueued = 0
    skipped_off_window = 0
    for campaign in campaigns:
        schedule_config = (campaign.orchestrator_metadata or {}).get("schedule_config")
        if not is_within_schedule(schedule_config, now=now, campaign_id=campaign.id):
            skipped_off_window += 1
            continue
        await enqueue_job(
            FunctionNames.PROCESS_CAMPAIGN_BATCH, campaign.id, _BATCH_SIZE
        )
        enqueued += 1
    if campaigns:
        logger.info(
            f"retry-dispatch cron: {len(campaigns)} campaign(s) with due retries, "
            f"{enqueued} batch(es) enqueued, {skipped_off_window} off-window skipped"
        )
    return {
        "campaigns": len(campaigns),
        "enqueued": enqueued,
        "skipped_off_window": skipped_off_window,
    }
