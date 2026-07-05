"""Retry dispatch-cron: enqueues a batch per running campaign with due,
in-window retries; skips off-window; safe no-op when nothing is due."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.tasks import retry_dispatch
from api.tasks.function_names import FunctionNames


def _campaign(cid, schedule_config=None):
    return SimpleNamespace(
        id=cid,
        orchestrator_metadata={"schedule_config": schedule_config} if schedule_config else {},
    )


# An always-open schedule (Mon-Sun 00:00-23:59 UTC) and an always-closed one.
_OPEN = {
    "enabled": True,
    "timezone": "UTC",
    "slots": [{"day_of_week": d, "start_time": "00:00", "end_time": "23:59"} for d in range(7)],
}
_CLOSED = {
    "enabled": True,
    "timezone": "UTC",
    "slots": [{"day_of_week": d, "start_time": "00:00", "end_time": "00:01"} for d in range(7)],
}


@pytest.mark.asyncio
async def test_enqueues_batch_for_in_window_campaign(monkeypatch):
    monkeypatch.setattr(
        retry_dispatch.db_client,
        "list_running_campaigns_with_due_retries",
        AsyncMock(return_value=[_campaign(1), _campaign(2, _OPEN)]),
    )
    enq = AsyncMock()
    monkeypatch.setattr(retry_dispatch, "enqueue_job", enq)

    result = await retry_dispatch.dispatch_due_campaign_retries(ctx={})

    assert result == {"campaigns": 2, "enqueued": 2, "skipped_off_window": 0}
    assert enq.await_count == 2
    enq.assert_any_await(FunctionNames.PROCESS_CAMPAIGN_BATCH, 1, 10)


@pytest.mark.asyncio
async def test_skips_off_window_campaign(monkeypatch):
    # now = 12:00 UTC, which is OUTSIDE the 00:00-00:01 closed window.
    monkeypatch.setattr(
        retry_dispatch.db_client,
        "list_running_campaigns_with_due_retries",
        AsyncMock(return_value=[_campaign(3, _CLOSED)]),
    )
    enq = AsyncMock()
    monkeypatch.setattr(retry_dispatch, "enqueue_job", enq)

    result = await retry_dispatch.dispatch_due_campaign_retries(ctx={})

    assert result == {"campaigns": 1, "enqueued": 0, "skipped_off_window": 1}
    enq.assert_not_awaited()


@pytest.mark.asyncio
async def test_noop_when_nothing_due(monkeypatch):
    monkeypatch.setattr(
        retry_dispatch.db_client,
        "list_running_campaigns_with_due_retries",
        AsyncMock(return_value=[]),
    )
    enq = AsyncMock()
    monkeypatch.setattr(retry_dispatch, "enqueue_job", enq)

    result = await retry_dispatch.dispatch_due_campaign_retries(ctx={})

    assert result == {"campaigns": 0, "enqueued": 0, "skipped_off_window": 0}
    enq.assert_not_awaited()


@pytest.mark.asyncio
async def test_campaign_without_schedule_is_fail_open(monkeypatch):
    # No schedule_config -> is_within_schedule fails open (allowed) -> enqueued.
    monkeypatch.setattr(
        retry_dispatch.db_client,
        "list_running_campaigns_with_due_retries",
        AsyncMock(return_value=[_campaign(5)]),
    )
    enq = AsyncMock()
    monkeypatch.setattr(retry_dispatch, "enqueue_job", enq)

    result = await retry_dispatch.dispatch_due_campaign_retries(ctx={})

    assert result["enqueued"] == 1
