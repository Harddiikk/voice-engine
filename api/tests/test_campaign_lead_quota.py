"""Lead quota ("call next N leads"): helpers, runner window, dispatcher gate."""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.campaign.lead_quota import (
    lead_quota_exhausted,
    lead_quota_remaining,
)


class TestLeadQuotaHelpers:
    def test_no_quota_means_unlimited(self):
        assert lead_quota_remaining(None) is None
        assert lead_quota_remaining({}) is None
        assert lead_quota_remaining({"lead_quota_used": 500}) is None
        assert lead_quota_exhausted(None) is False
        assert lead_quota_exhausted({"lead_quota_used": 500}) is False

    def test_remaining_counts_down(self):
        assert lead_quota_remaining({"lead_quota": 100}) == 100
        assert lead_quota_remaining({"lead_quota": 100, "lead_quota_used": 40}) == 60
        assert lead_quota_remaining({"lead_quota": 100, "lead_quota_used": 100}) == 0

    def test_used_beyond_quota_clamps_to_zero(self):
        assert lead_quota_remaining({"lead_quota": 10, "lead_quota_used": 25}) == 0

    def test_exhausted_at_or_over_quota(self):
        assert lead_quota_exhausted({"lead_quota": 10, "lead_quota_used": 9}) is False
        assert lead_quota_exhausted({"lead_quota": 10, "lead_quota_used": 10}) is True
        assert lead_quota_exhausted({"lead_quota": 10, "lead_quota_used": 11}) is True


class _Campaign:
    def __init__(self, meta=None, campaign_id=7):
        self.id = campaign_id
        self.orchestrator_metadata = meta


class TestOpenQuotaWindow:
    def test_call_limit_sets_quota_and_resets_used(self):
        from api.services.campaign.lead_quota import open_quota_window

        meta, changed = open_quota_window(
            {"lead_quota": 50, "lead_quota_used": 50}, 200
        )
        assert changed is True
        assert meta["lead_quota"] == 200
        assert "lead_quota_used" not in meta

    def test_no_call_limit_clears_previous_quota(self):
        from api.services.campaign.lead_quota import open_quota_window

        meta, changed = open_quota_window(
            {"lead_quota": 50, "lead_quota_used": 12}, None
        )
        assert changed is True
        assert "lead_quota" not in meta
        assert "lead_quota_used" not in meta

    def test_no_call_limit_and_no_prior_quota_changes_nothing(self):
        from api.services.campaign.lead_quota import open_quota_window

        meta, changed = open_quota_window({"max_concurrency": 3}, None)
        assert changed is False
        assert meta == {"max_concurrency": 3}

    def test_quota_window_preserves_other_metadata(self):
        from api.services.campaign.lead_quota import open_quota_window

        meta, changed = open_quota_window(
            {"max_concurrency": 3, "budget_seconds": 600}, 100
        )
        assert changed is True
        assert meta["max_concurrency"] == 3
        assert meta["budget_seconds"] == 600
        assert meta["lead_quota"] == 100

    def test_none_metadata_input(self):
        from api.services.campaign.lead_quota import open_quota_window

        meta, changed = open_quota_window(None, 100)
        assert changed is True
        assert meta == {"lead_quota": 100}


class TestDispatcherQuotaGate:
    @pytest.mark.asyncio
    async def test_exhausted_quota_pauses_before_claiming(self):
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
        )

        campaign = _Campaign({"lead_quota": 5, "lead_quota_used": 5})
        campaign.state = "running"
        campaign.organization_id = 1

        with (
            patch(
                "api.services.campaign.campaign_call_dispatcher.db_client"
            ) as db,
            patch(
                "api.services.campaign.campaign_call_dispatcher.has_free_call_seconds",
                AsyncMock(return_value=True),
            ),
        ):
            db.get_campaign_by_id = AsyncMock(return_value=campaign)
            db.update_campaign = AsyncMock()
            db.append_campaign_log = AsyncMock()
            db.claim_queued_runs_for_processing = AsyncMock()

            processed = await CampaignCallDispatcher().process_batch(7)

        assert processed == 0
        db.update_campaign.assert_awaited_once_with(campaign_id=7, state="paused")
        db.append_campaign_log.assert_awaited_once()
        db.claim_queued_runs_for_processing.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_remaining_quota_caps_new_lead_claims(self):
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
        )

        campaign = _Campaign({"lead_quota": 5, "lead_quota_used": 3})
        campaign.state = "running"
        campaign.organization_id = 1

        with (
            patch(
                "api.services.campaign.campaign_call_dispatcher.db_client"
            ) as db,
            patch(
                "api.services.campaign.campaign_call_dispatcher.has_free_call_seconds",
                AsyncMock(return_value=True),
            ),
        ):
            db.get_campaign_by_id = AsyncMock(return_value=campaign)
            db.claim_queued_runs_for_processing = AsyncMock(return_value=[])

            processed = await CampaignCallDispatcher().process_batch(
                7, batch_size=10
            )

        assert processed == 0
        claim_kwargs = db.claim_queued_runs_for_processing.await_args.kwargs
        assert claim_kwargs["new_lead_limit"] == 2
        assert claim_kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_no_quota_passes_none_lead_limit(self):
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
        )

        campaign = _Campaign({})
        campaign.state = "running"
        campaign.organization_id = 1

        with (
            patch(
                "api.services.campaign.campaign_call_dispatcher.db_client"
            ) as db,
            patch(
                "api.services.campaign.campaign_call_dispatcher.has_free_call_seconds",
                AsyncMock(return_value=True),
            ),
        ):
            db.get_campaign_by_id = AsyncMock(return_value=campaign)
            db.claim_queued_runs_for_processing = AsyncMock(return_value=[])

            await CampaignCallDispatcher().process_batch(7)

        claim_kwargs = db.claim_queued_runs_for_processing.await_args.kwargs
        assert claim_kwargs["new_lead_limit"] is None
