"""Adaptive concurrency: degrade and keep running instead of pausing.

A tripped circuit breaker used to pause the campaign outright. It now halves
concurrency and keeps dialing, pausing only once it is already at one call at
a time and still failing. Throttled campaigns climb back one slot at a time.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.campaign import concurrency as conc


def _campaign(metadata=None, org_id=1, telephony_id=7, campaign_id=42):
    return SimpleNamespace(
        id=campaign_id,
        organization_id=org_id,
        telephony_configuration_id=telephony_id,
        orchestrator_metadata=metadata if metadata is not None else {},
    )


def _limits(org_limit=10, channel_capacity=10):
    return (
        patch.object(
            conc, "get_org_concurrent_limit", AsyncMock(return_value=org_limit)
        ),
        patch.object(
            conc, "get_channel_capacity", AsyncMock(return_value=channel_capacity)
        ),
    )


class TestThrottleCapsResolution:
    @pytest.mark.asyncio
    async def test_active_throttle_lowers_the_dialed_concurrency(self):
        campaign = _campaign({"max_concurrency": 8, conc.THROTTLE_KEY: {"value": 2}})
        p1, p2 = _limits()
        with p1, p2:
            assert await conc.resolve_campaign_concurrency(campaign) == 2

    @pytest.mark.asyncio
    async def test_throttle_never_raises_above_the_other_ceilings(self):
        """A stale throttle above the trunk's channels must not win."""
        campaign = _campaign({"max_concurrency": 8, conc.THROTTLE_KEY: {"value": 9}})
        p1, p2 = _limits(org_limit=10, channel_capacity=4)
        with p1, p2:
            assert await conc.resolve_campaign_concurrency(campaign) == 4

    @pytest.mark.asyncio
    async def test_malformed_throttle_is_ignored(self):
        for bad in [{"value": 0}, {}, "nonsense", None]:
            campaign = _campaign({"max_concurrency": 5, conc.THROTTLE_KEY: bad})
            p1, p2 = _limits()
            with p1, p2:
                assert await conc.resolve_campaign_concurrency(campaign) == 5


class TestApplyThrottle:
    @pytest.mark.asyncio
    async def test_halves_current_concurrency(self):
        campaign = _campaign({"max_concurrency": 8})
        write = AsyncMock(return_value=True)
        p1, p2 = _limits()
        with p1, p2, patch.object(conc, "_write_throttle", write):
            assert await conc.apply_throttle(campaign, "boom") == 4
        assert write.await_args.args[1]["value"] == 4
        assert write.await_args.args[1]["previous"] == 8

    @pytest.mark.asyncio
    async def test_halving_rounds_down_but_floors_at_one(self):
        campaign = _campaign({"max_concurrency": 3})
        p1, p2 = _limits()
        with p1, p2, patch.object(conc, "_write_throttle", AsyncMock(return_value=True)):
            assert await conc.apply_throttle(campaign, "boom") == 1

    @pytest.mark.asyncio
    async def test_returns_none_at_one_so_caller_pauses(self):
        """The signal that backing off further isn't possible."""
        campaign = _campaign({"max_concurrency": 1})
        p1, p2 = _limits()
        with p1, p2:
            assert await conc.apply_throttle(campaign, "boom") is None

    @pytest.mark.asyncio
    async def test_failed_write_reports_no_throttle(self):
        campaign = _campaign({"max_concurrency": 8})
        p1, p2 = _limits()
        with p1, p2, patch.object(conc, "_write_throttle", AsyncMock(return_value=False)):
            assert await conc.apply_throttle(campaign, "boom") is None


class TestRecoverThrottle:
    @pytest.mark.asyncio
    async def test_steps_up_one_slot_at_a_time(self):
        campaign = _campaign({"max_concurrency": 8, conc.THROTTLE_KEY: {"value": 2}})
        write = AsyncMock(return_value=True)
        with patch.object(conc, "_write_throttle", write):
            assert await conc.recover_throttle(campaign) == 3
        assert write.await_args.args[1]["value"] == 3

    @pytest.mark.asyncio
    async def test_clears_throttle_on_reaching_configured_value(self):
        campaign = _campaign({"max_concurrency": 4, conc.THROTTLE_KEY: {"value": 3}})
        write = AsyncMock(return_value=True)
        with patch.object(conc, "_write_throttle", write):
            assert await conc.recover_throttle(campaign) == 4
        # None = remove the key entirely, so a healed campaign looks normal.
        assert write.await_args.args[1] is None

    @pytest.mark.asyncio
    async def test_clears_a_throttle_that_exceeds_the_configured_value(self):
        campaign = _campaign({"max_concurrency": 2, conc.THROTTLE_KEY: {"value": 5}})
        write = AsyncMock(return_value=True)
        with patch.object(conc, "_write_throttle", write):
            assert await conc.recover_throttle(campaign) == 2
        assert write.await_args.args[1] is None

    @pytest.mark.asyncio
    async def test_untouched_campaign_has_nothing_to_recover(self):
        with patch.object(conc, "_write_throttle", AsyncMock(return_value=True)) as w:
            assert await conc.recover_throttle(_campaign({"max_concurrency": 4})) is None
        w.assert_not_awaited()


class TestCircuitBreakerDegradesBeforePausing:
    @pytest.mark.asyncio
    async def test_trip_reduces_concurrency_and_keeps_running(self):
        from api.services.campaign import circuit_breaker as cb_mod

        campaign = _campaign({"max_concurrency": 8})
        stats = {
            "failure_rate": 1.0,
            "failure_count": 20,
            "success_count": 0,
            "threshold": 0.9,
            "window_seconds": 120,
        }
        breaker = cb_mod.CircuitBreaker()

        with (
            patch.object(cb_mod, "apply_throttle", AsyncMock(return_value=4)),
            patch.object(breaker, "reset", AsyncMock(return_value=True)),
            patch.object(breaker, "_get_recent_failures", AsyncMock(return_value=[])),
            patch.object(breaker, "_get_redis", AsyncMock()),
            patch.object(cb_mod.db_client, "append_campaign_log", AsyncMock()) as log,
        ):
            survived = await breaker._throttle_instead_of_pause(campaign, stats)

        assert survived is True
        assert log.await_args.kwargs["event"] == "concurrency_reduced"

    @pytest.mark.asyncio
    async def test_falls_through_to_pause_when_already_at_one(self):
        from api.services.campaign import circuit_breaker as cb_mod

        campaign = _campaign({"max_concurrency": 1})
        stats = {
            "failure_rate": 1.0,
            "failure_count": 20,
            "success_count": 0,
            "threshold": 0.9,
            "window_seconds": 120,
        }
        breaker = cb_mod.CircuitBreaker()

        with patch.object(cb_mod, "apply_throttle", AsyncMock(return_value=None)):
            survived = await breaker._throttle_instead_of_pause(campaign, stats)

        assert survived is False  # caller pauses

    @pytest.mark.asyncio
    async def test_throttle_errors_fall_back_to_pausing(self):
        """A broken throttle must never leave a failing campaign dialing on."""
        from api.services.campaign import circuit_breaker as cb_mod

        breaker = cb_mod.CircuitBreaker()
        stats = {
            "failure_rate": 1.0,
            "failure_count": 20,
            "success_count": 0,
            "threshold": 0.9,
            "window_seconds": 120,
        }
        with patch.object(
            cb_mod, "apply_throttle", AsyncMock(side_effect=RuntimeError("db down"))
        ):
            survived = await breaker._throttle_instead_of_pause(_campaign(), stats)

        assert survived is False

    @pytest.mark.asyncio
    async def test_disabled_flag_restores_pause_on_trip_behaviour(self):
        from api.services.campaign import circuit_breaker as cb_mod

        breaker = cb_mod.CircuitBreaker()
        with patch.object(cb_mod, "CAMPAIGN_ADAPTIVE_CONCURRENCY", False):
            survived = await breaker._throttle_instead_of_pause(_campaign(), {})
        assert survived is False


class TestRecoveryStreak:
    @pytest.mark.asyncio
    async def test_failure_resets_the_streak(self):
        from api.services.campaign import circuit_breaker as cb_mod

        breaker = cb_mod.CircuitBreaker()
        redis = AsyncMock()
        with patch.object(breaker, "_get_redis", AsyncMock(return_value=redis)):
            await breaker._track_recovery(_campaign(), is_failure=True)
        redis.delete.assert_awaited_once()
        redis.incr.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_below_threshold_does_not_step_up(self):
        from api.services.campaign import circuit_breaker as cb_mod

        breaker = cb_mod.CircuitBreaker()
        redis = AsyncMock()
        redis.incr = AsyncMock(return_value=3)
        campaign = _campaign({"max_concurrency": 8, conc.THROTTLE_KEY: {"value": 2}})

        with (
            patch.object(breaker, "_get_redis", AsyncMock(return_value=redis)),
            patch.object(cb_mod, "CAMPAIGN_CONCURRENCY_RECOVERY_SUCCESSES", 10),
            patch.object(cb_mod, "recover_throttle", AsyncMock()) as rec,
        ):
            await breaker._track_recovery(campaign, is_failure=False)
        rec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_streak_reaching_threshold_steps_up(self):
        from api.services.campaign import circuit_breaker as cb_mod

        breaker = cb_mod.CircuitBreaker()
        redis = AsyncMock()
        redis.incr = AsyncMock(return_value=10)
        campaign = _campaign({"max_concurrency": 8, conc.THROTTLE_KEY: {"value": 2}})

        with (
            patch.object(breaker, "_get_redis", AsyncMock(return_value=redis)),
            patch.object(cb_mod, "CAMPAIGN_CONCURRENCY_RECOVERY_SUCCESSES", 10),
            patch.object(cb_mod, "recover_throttle", AsyncMock(return_value=3)),
            patch.object(cb_mod.db_client, "append_campaign_log", AsyncMock()) as log,
        ):
            await breaker._track_recovery(campaign, is_failure=False)

        assert log.await_args.kwargs["event"] == "concurrency_restored"

    @pytest.mark.asyncio
    async def test_healthy_campaign_does_not_touch_the_counter(self):
        from api.services.campaign import circuit_breaker as cb_mod

        breaker = cb_mod.CircuitBreaker()
        redis = AsyncMock()
        with patch.object(breaker, "_get_redis", AsyncMock(return_value=redis)):
            await breaker._track_recovery(
                _campaign({"max_concurrency": 8}), is_failure=False
            )
        redis.incr.assert_not_awaited()


class TestSlotTimeoutKeepsCampaignAlive:
    """A busy moment must not strand a campaign in "failed" with work pending."""

    @staticmethod
    def _harness(attempt):
        """Patch the batch task so the slot timeout is the only thing happening."""
        from api.services.campaign.errors import ConcurrentSlotAcquisitionError
        from api.tasks import campaign_tasks as tasks

        mocks = SimpleNamespace(
            update_campaign=AsyncMock(),
            append_campaign_log=AsyncMock(),
            publisher=AsyncMock(),
        )
        err = ConcurrentSlotAcquisitionError(
            organization_id=1, campaign_id=42, wait_time=600.0
        )
        ctx = patch.multiple(
            tasks.db_client,
            increment_campaign_metadata_counter=AsyncMock(return_value=attempt),
            update_campaign=mocks.update_campaign,
            append_campaign_log=mocks.append_campaign_log,
            reset_campaign_metadata_counter=AsyncMock(),
        )
        return tasks, err, mocks, ctx

    @pytest.mark.asyncio
    async def test_early_timeout_keeps_campaign_running(self):
        tasks, err, mocks, db_patch = self._harness(attempt=1)

        with (
            db_patch,
            patch.object(
                tasks.campaign_call_dispatcher,
                "process_batch",
                AsyncMock(side_effect=err),
            ),
            patch.object(
                tasks,
                "get_campaign_event_publisher",
                AsyncMock(return_value=mocks.publisher),
            ),
        ):
            # Must NOT raise — raising is what marked the campaign failed.
            await tasks.process_campaign_batch({}, campaign_id=42, batch_size=10)

        mocks.update_campaign.assert_not_awaited()  # still "running"
        assert (
            mocks.append_campaign_log.await_args.kwargs["event"]
            == "concurrent_slot_timeout_retry"
        )
        mocks.publisher.publish_batch_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_persistent_timeout_eventually_fails_the_campaign(self):
        from api.tasks.campaign_tasks import MAX_CONCURRENT_SLOT_TIMEOUT_ATTEMPTS

        tasks, err, mocks, db_patch = self._harness(
            attempt=MAX_CONCURRENT_SLOT_TIMEOUT_ATTEMPTS
        )

        with (
            db_patch,
            patch.object(
                tasks.campaign_call_dispatcher,
                "process_batch",
                AsyncMock(side_effect=err),
            ),
            patch.object(
                tasks,
                "get_campaign_event_publisher",
                AsyncMock(return_value=mocks.publisher),
            ),
            pytest.raises(Exception),
        ):
            await tasks.process_campaign_batch({}, campaign_id=42, batch_size=10)

        mocks.update_campaign.assert_awaited_once()
        assert mocks.update_campaign.await_args.kwargs["state"] == "failed"


class TestDefaultCallingWindow:
    def test_defaults_to_9am_to_8pm_kolkata_every_day(self):
        from api.services.campaign.schedule import default_schedule_config

        config = default_schedule_config()
        assert config["enabled"] is True
        assert config["timezone"] == "Asia/Kolkata"
        assert len(config["slots"]) == 7
        assert {s["start_time"] for s in config["slots"]} == {"09:00"}
        assert {s["end_time"] for s in config["slots"]} == {"20:00"}
        assert sorted(s["day_of_week"] for s in config["slots"]) == list(range(7))

    def test_calls_are_blocked_after_8pm_kolkata(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from api.services.campaign.schedule import (
            default_schedule_config,
            is_within_schedule,
        )

        config = default_schedule_config()
        kolkata = ZoneInfo("Asia/Kolkata")

        assert is_within_schedule(config, now=datetime(2026, 7, 30, 19, 59, tzinfo=kolkata))
        assert not is_within_schedule(config, now=datetime(2026, 7, 30, 20, 0, tzinfo=kolkata))
        assert not is_within_schedule(config, now=datetime(2026, 7, 30, 23, 30, tzinfo=kolkata))
        assert not is_within_schedule(config, now=datetime(2026, 7, 30, 8, 59, tzinfo=kolkata))
        assert is_within_schedule(config, now=datetime(2026, 7, 30, 9, 0, tzinfo=kolkata))

    def test_window_is_evaluated_in_kolkata_not_utc(self):
        """16:00 UTC is 21:30 in Kolkata — must be out of window."""
        from datetime import UTC, datetime

        from api.services.campaign.schedule import (
            default_schedule_config,
            is_within_schedule,
        )

        config = default_schedule_config()
        assert not is_within_schedule(
            config, now=datetime(2026, 7, 30, 16, 0, tzinfo=UTC)
        )
