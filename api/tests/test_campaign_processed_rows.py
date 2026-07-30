"""Batch progress accounting.

process_batch holds ONE campaign snapshot for the whole batch, so the old
``update_campaign(processed_rows=campaign.processed_rows + 1)`` after every
dispatch re-wrote the same stale value each time: the counter advanced by 1 per
BATCH rather than per call. Production showed the damage — campaign 4 read 79
against 773 genuinely-processed runs, almost exactly batch_size (10x) out.

It now accumulates in memory and flushes once per batch through an atomic SQL
increment: correct, and one write per batch instead of one per dispatched call.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher


class TestFlushProcessedCount:
    @pytest.mark.asyncio
    async def test_flushes_the_whole_batch_in_one_increment(self):
        dispatcher = CampaignCallDispatcher()
        increment = AsyncMock(return_value=783)

        with patch(
            "api.services.campaign.campaign_call_dispatcher.db_client"
        ) as mock_db:
            mock_db.increment_campaign_processed_rows = increment
            await dispatcher._flush_processed_count(campaign_id=4, processed_count=10)

        # One write for ten calls, and it adds 10 — not "stale + 1".
        increment.assert_awaited_once_with(4, 10)

    @pytest.mark.asyncio
    async def test_no_write_when_nothing_was_dispatched(self):
        dispatcher = CampaignCallDispatcher()
        increment = AsyncMock()

        with patch(
            "api.services.campaign.campaign_call_dispatcher.db_client"
        ) as mock_db:
            mock_db.increment_campaign_processed_rows = increment
            await dispatcher._flush_processed_count(campaign_id=4, processed_count=0)

        increment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_counter_failure_never_breaks_a_batch_that_dialed(self):
        """The calls already went out; a bad counter must not surface as failure."""
        dispatcher = CampaignCallDispatcher()

        with patch(
            "api.services.campaign.campaign_call_dispatcher.db_client"
        ) as mock_db:
            mock_db.increment_campaign_processed_rows = AsyncMock(
                side_effect=RuntimeError("db down")
            )
            await dispatcher._flush_processed_count(campaign_id=4, processed_count=5)


class TestIncrementIsAtomic:
    @pytest.mark.asyncio
    async def test_negative_or_zero_delta_is_a_no_op(self):
        """Guards the SQL from running with a meaningless delta."""
        from api.db.campaign_client import CampaignClient

        client = CampaignClient.__new__(CampaignClient)
        assert await client.increment_campaign_processed_rows(1, 0) == 0
        assert await client.increment_campaign_processed_rows(1, -3) == 0
