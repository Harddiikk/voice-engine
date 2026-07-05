"""Campaign voicemail follow-up trigger.

Busy/no-answer retries fire from the telephony status processor, but voicemail
is detected in-pipeline (the call connects, then hangs up) so it never reaches
that path. This is the missing trigger for a campaign's ``retry_on_voicemail``
setting — called from the post-call task; the orchestrator decides eligibility.
"""

from loguru import logger
from pipecat.utils.enums import EndTaskReason

from api.db.models import WorkflowRunModel


async def maybe_publish_voicemail_retry(workflow_run: WorkflowRunModel) -> bool:
    """Publish a campaign retry event when a campaign call ended in voicemail.

    The orchestrator decides eligibility (retry_on_voicemail + max_retries +
    delay); the unique (campaign_id, source_uuid, retry_count) constraint
    backstops any duplicate. Returns True if an event was published.
    Best-effort: never raises.
    """
    if not getattr(workflow_run, "campaign_id", None):
        return False
    try:
        gathered = workflow_run.gathered_context or {}
        if gathered.get("call_disposition") != EndTaskReason.VOICEMAIL_DETECTED.value:
            return False
        from api.services.campaign.campaign_event_publisher import (
            get_campaign_event_publisher,
        )

        publisher = await get_campaign_event_publisher()
        await publisher.publish_retry_needed(
            workflow_run_id=workflow_run.id,
            reason="voicemail",
            campaign_id=workflow_run.campaign_id,
            queued_run_id=workflow_run.queued_run_id,
        )
        logger.info(
            f"[run {workflow_run.id}] Voicemail — published campaign retry event "
            f"(campaign {workflow_run.campaign_id})"
        )
        return True
    except Exception as exc:
        logger.warning(
            f"Voicemail retry publish failed for run {workflow_run.id}: {exc}"
        )
        return False
