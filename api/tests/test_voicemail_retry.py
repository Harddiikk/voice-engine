"""Phase 1: a campaign call that ends in voicemail publishes a retry event
(the trigger the orchestrator's retry_on_voicemail check was missing)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.campaign.voicemail_retry import maybe_publish_voicemail_retry


def _run(**overrides):
    defaults = {
        "id": 55,
        "campaign_id": 9,
        "queued_run_id": 77,
        "gathered_context": {"call_disposition": "voicemail_detected"},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_voicemail_campaign_run_publishes_retry():
    publisher = SimpleNamespace(publish_retry_needed=AsyncMock())
    with patch(
        "api.services.campaign.campaign_event_publisher.get_campaign_event_publisher",
        new=AsyncMock(return_value=publisher),
    ):
        published = await maybe_publish_voicemail_retry(_run())
    assert published is True
    publisher.publish_retry_needed.assert_awaited_once_with(
        workflow_run_id=55, reason="voicemail", campaign_id=9, queued_run_id=77
    )


@pytest.mark.asyncio
async def test_non_voicemail_disposition_does_not_publish():
    publisher = SimpleNamespace(publish_retry_needed=AsyncMock())
    with patch(
        "api.services.campaign.campaign_event_publisher.get_campaign_event_publisher",
        new=AsyncMock(return_value=publisher),
    ):
        published = await maybe_publish_voicemail_retry(
            _run(gathered_context={"call_disposition": "user_hangup"})
        )
    assert published is False
    publisher.publish_retry_needed.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_campaign_run_does_not_publish():
    published = await maybe_publish_voicemail_retry(_run(campaign_id=None))
    assert published is False


@pytest.mark.asyncio
async def test_publish_failure_is_swallowed():
    with patch(
        "api.services.campaign.campaign_event_publisher.get_campaign_event_publisher",
        new=AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        # Best-effort: must not raise into the post-call pipeline.
        published = await maybe_publish_voicemail_retry(_run())
    assert published is False
