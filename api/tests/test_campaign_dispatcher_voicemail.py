"""Tests that the campaign dispatcher propagates the per-campaign
``hangup_on_voicemail`` toggle onto each run's ``initial_context``.

The toggle lives in ``campaign.orchestrator_metadata`` and must land in the
workflow run's ``initial_context`` so ``run_pipeline`` can override the
workflow's voicemail_detection default for this campaign's calls.

These tests exercise ``CampaignCallDispatcher.dispatch_call`` with the DB and
rate limiter mocked (no real Postgres/Redis). ``create_workflow_run`` is stubbed
to raise a sentinel immediately after the dispatcher builds ``initial_context``,
so we can assert on the captured kwargs without driving the full dial path.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher


class _StopAfterCreate(Exception):
    """Sentinel raised by the mocked create_workflow_run to short-circuit
    dispatch_call right after initial_context is assembled."""


def _make_campaign(orchestrator_metadata):
    campaign = MagicMock()
    campaign.id = 42
    campaign.workflow_id = 7
    campaign.organization_id = 3
    campaign.created_by = 5
    campaign.telephony_configuration_id = 11
    campaign.orchestrator_metadata = orchestrator_metadata
    return campaign


def _make_queued_run():
    queued_run = MagicMock()
    queued_run.id = 100
    queued_run.source_uuid = "uuid-x"
    queued_run.context_variables = {"phone_number": "+15559876543"}
    return queued_run


async def _capture_initial_context(orchestrator_metadata) -> dict:
    """Run dispatch_call with everything mocked and return the initial_context
    dict the dispatcher passed to create_workflow_run."""
    dispatcher = CampaignCallDispatcher()

    provider = MagicMock()
    provider.PROVIDER_NAME = "twilio"

    # Stub the two async instance methods dispatch_call calls before it builds
    # initial_context.
    dispatcher.get_provider_for_campaign = AsyncMock(return_value=provider)
    dispatcher.acquire_from_number = AsyncMock(return_value="+15551234567#0")

    mock_db = MagicMock()
    mock_db.get_workflow_by_id = AsyncMock(return_value=MagicMock())
    mock_db.create_workflow_run = AsyncMock(side_effect=_StopAfterCreate())

    mock_rl = MagicMock()
    mock_rl.bare_from_number = MagicMock(return_value="+15551234567")
    mock_rl.release_concurrent_slot = AsyncMock()
    mock_rl.release_from_number = AsyncMock()

    with (
        patch(
            "api.services.campaign.campaign_call_dispatcher.db_client",
            mock_db,
        ),
        patch(
            "api.services.campaign.campaign_call_dispatcher.rate_limiter",
            mock_rl,
        ),
    ):
        with pytest.raises(_StopAfterCreate):
            await dispatcher.dispatch_call(
                _make_queued_run(), _make_campaign(orchestrator_metadata), "slot-1"
            )

    mock_db.create_workflow_run.assert_awaited_once()
    return mock_db.create_workflow_run.await_args.kwargs["initial_context"]


class TestDispatcherHangupOnVoicemail:
    @pytest.mark.asyncio
    async def test_toggle_true_propagates(self):
        ctx = await _capture_initial_context({"hangup_on_voicemail": True})
        assert ctx["hangup_on_voicemail"] is True

    @pytest.mark.asyncio
    async def test_toggle_false_propagates(self):
        ctx = await _capture_initial_context({"hangup_on_voicemail": False})
        assert ctx["hangup_on_voicemail"] is False

    @pytest.mark.asyncio
    async def test_toggle_absent_is_none(self):
        # No override set on the campaign => None, so run_pipeline falls back to
        # the workflow's voicemail_detection.enabled default.
        ctx = await _capture_initial_context({"max_concurrency": 5})
        assert ctx["hangup_on_voicemail"] is None

    @pytest.mark.asyncio
    async def test_null_orchestrator_metadata_is_none(self):
        ctx = await _capture_initial_context(None)
        assert ctx["hangup_on_voicemail"] is None
