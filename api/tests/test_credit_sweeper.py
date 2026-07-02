"""Leak sweeper: cutoff windows, per-run settlement with origin='sweeper',
resilience to per-run failures."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.tasks import credit_sweeper
from api.tasks.credit_sweeper import settle_leaked_credit_holds


def _run(run_id):
    return SimpleNamespace(id=run_id, reserved_credit_seconds=600)


async def test_sweeper_noop_when_no_leaks():
    with patch.object(
        credit_sweeper.db_client,
        "list_unsettled_credit_holds",
        new=AsyncMock(return_value=[]),
    ):
        assert await settle_leaked_credit_holds({}) == {"leaked": 0, "settled": 0}


async def test_sweeper_settles_each_leak_with_sweeper_origin():
    runs = [(_run(1), 4), (_run(2), 5)]
    settle = AsyncMock(return_value="settled")
    with (
        patch.object(
            credit_sweeper.db_client,
            "list_unsettled_credit_holds",
            new=AsyncMock(return_value=runs),
        ),
        patch.object(credit_sweeper, "settle_workflow_run_credits", new=settle),
    ):
        result = await settle_leaked_credit_holds({})

    assert result == {"leaked": 2, "settled": 2}
    assert settle.await_count == 2
    first, second = settle.await_args_list
    assert first.args[0] == 4 and first.args[1].id == 1
    assert second.args[0] == 5 and second.args[1].id == 2
    assert all(c.kwargs["origin"] == "sweeper" for c in settle.await_args_list)


async def test_sweeper_passes_grace_and_stale_cutoffs():
    lst = AsyncMock(return_value=[])
    before = datetime.now(UTC)
    with patch.object(credit_sweeper.db_client, "list_unsettled_credit_holds", new=lst):
        await settle_leaked_credit_holds({})
    after = datetime.now(UTC)

    kwargs = lst.await_args.kwargs
    completed_cutoff = kwargs["completed_cutoff"]
    stale_cutoff = kwargs["stale_cutoff"]
    assert (
        before - timedelta(minutes=30)
        <= completed_cutoff
        <= after - timedelta(minutes=30)
    )
    assert before - timedelta(hours=6) <= stale_cutoff <= after - timedelta(hours=6)


async def test_sweeper_survives_per_run_failures_and_already_settled():
    runs = [(_run(1), 4), (_run(2), 4), (_run(3), 4)]
    settle = AsyncMock(side_effect=[RuntimeError("db down"), "already", "settled"])
    with (
        patch.object(
            credit_sweeper.db_client,
            "list_unsettled_credit_holds",
            new=AsyncMock(return_value=runs),
        ),
        patch.object(credit_sweeper, "settle_workflow_run_credits", new=settle),
    ):
        result = await settle_leaked_credit_holds({})

    assert settle.await_count == 3  # a failure must not stop the sweep
    assert result == {"leaked": 3, "settled": 1}
