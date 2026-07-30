"""Concurrency resolution: campaign setting vs org ceiling vs trunk channels.

The regression these guard: creation-time validation capped max_concurrency at
the trunk's channel capacity, but the dispatcher dialed at
min(campaign, org_limit) and ignored the trunk. On a 4-channel trunk with the
default org limit of 5, a campaign with no stored max_concurrency ran at 5 and
the carrier rejected the 5th call.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.campaign import concurrency as conc


def _campaign(max_concurrency=None, org_id=1, telephony_id=7):
    metadata = {} if max_concurrency is None else {"max_concurrency": max_concurrency}
    return SimpleNamespace(
        id=42,
        organization_id=org_id,
        telephony_configuration_id=telephony_id,
        orchestrator_metadata=metadata,
    )


def _patch(org_limit, channel_capacity):
    """Patch the two DB-backed lookups resolve_campaign_concurrency depends on."""
    return (
        patch.object(
            conc, "get_org_concurrent_limit", AsyncMock(return_value=org_limit)
        ),
        patch.object(
            conc, "get_channel_capacity", AsyncMock(return_value=channel_capacity)
        ),
    )


class TestResolveCampaignConcurrency:
    @pytest.mark.asyncio
    async def test_unset_campaign_uses_platform_default_not_org_limit(self):
        """The original bug: no stored value used to mean 'dial at the org limit'."""
        p1, p2 = _patch(org_limit=5, channel_capacity=5)
        with p1, p2:
            assert await conc.resolve_campaign_concurrency(_campaign()) == 2

    @pytest.mark.asyncio
    async def test_channel_capacity_caps_the_org_limit(self):
        """4-channel trunk + org limit 5 + campaign asking 5 => dial 4, not 5."""
        p1, p2 = _patch(org_limit=5, channel_capacity=4)
        with p1, p2:
            resolved = await conc.resolve_campaign_concurrency(_campaign(5))
            assert resolved == 4

    @pytest.mark.asyncio
    async def test_campaign_setting_wins_when_it_is_the_smallest(self):
        p1, p2 = _patch(org_limit=10, channel_capacity=8)
        with p1, p2:
            assert await conc.resolve_campaign_concurrency(_campaign(3)) == 3

    @pytest.mark.asyncio
    async def test_org_limit_wins_when_it_is_the_smallest(self):
        p1, p2 = _patch(org_limit=2, channel_capacity=8)
        with p1, p2:
            assert await conc.resolve_campaign_concurrency(_campaign(6)) == 2

    @pytest.mark.asyncio
    async def test_unknown_capacity_falls_back_to_org_limit(self):
        """0 capacity = no active numbers = unknown; don't cap on it."""
        p1, p2 = _patch(org_limit=5, channel_capacity=0)
        with p1, p2:
            assert await conc.resolve_campaign_concurrency(_campaign(5)) == 5

    @pytest.mark.asyncio
    async def test_never_resolves_below_one(self):
        """A 0 would make the dispatcher wait for a slot forever."""
        p1, p2 = _patch(org_limit=0, channel_capacity=0)
        with p1, p2:
            assert await conc.resolve_campaign_concurrency(_campaign(1)) == 1

    @pytest.mark.asyncio
    async def test_missing_orchestrator_metadata_is_tolerated(self):
        campaign = SimpleNamespace(
            id=1,
            organization_id=1,
            telephony_configuration_id=None,
            orchestrator_metadata=None,
        )
        p1, p2 = _patch(org_limit=5, channel_capacity=4)
        with p1, p2:
            assert await conc.resolve_campaign_concurrency(campaign) == 2

    @pytest.mark.asyncio
    async def test_capacity_is_looked_up_for_the_campaigns_own_trunk(self):
        """Not the org default trunk — campaigns pin a telephony config."""
        p1 = patch.object(
            conc, "get_org_concurrent_limit", AsyncMock(return_value=5)
        )
        capacity_mock = AsyncMock(return_value=4)
        p2 = patch.object(conc, "get_channel_capacity", capacity_mock)
        with p1, p2:
            await conc.resolve_campaign_concurrency(_campaign(5, org_id=9, telephony_id=3))
        capacity_mock.assert_awaited_once_with(9, 3)


class TestDispatcherUsesResolvedConcurrency:
    """The wiring, not just the arithmetic — this is what actually regressed."""

    @pytest.mark.asyncio
    async def test_slot_request_uses_channel_capped_value(self):
        from api.services.campaign import campaign_call_dispatcher as dispatcher_mod

        campaign = _campaign(5)  # asks for 5 on a 4-channel trunk
        slot_mock = AsyncMock(return_value="slot-1")

        with (
            patch.object(
                dispatcher_mod,
                "resolve_campaign_concurrency",
                AsyncMock(return_value=4),
            ),
            patch.object(dispatcher_mod, "rate_limiter") as rl,
        ):
            rl.try_acquire_concurrent_slot = slot_mock
            slot = await dispatcher_mod.CampaignCallDispatcher().acquire_concurrent_slot(
                organization_id=9, campaign=campaign
            )

        assert slot == "slot-1"
        # 4 (the trunk's channels), not 5 (what the campaign asked for).
        slot_mock.assert_awaited_once_with(9, 4)


class TestGetEffectiveLimit:
    @pytest.mark.asyncio
    async def test_takes_the_smaller_of_org_and_channels(self):
        p1, p2 = _patch(org_limit=5, channel_capacity=4)
        with p1, p2:
            assert await conc.get_effective_limit(1, 2) == 4

    @pytest.mark.asyncio
    async def test_ignores_zero_capacity(self):
        p1, p2 = _patch(org_limit=5, channel_capacity=0)
        with p1, p2:
            assert await conc.get_effective_limit(1, 2) == 5
