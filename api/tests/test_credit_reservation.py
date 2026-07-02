"""Credit reservation + settlement service logic (the _tx layer is mocked).

Covers: run-scoped reserve (unmetered / sufficient / insufficient / retried),
the legacy run-less reserve, legacy reconcile, and settle_workflow_run_credits
(column-vs-JSON reserved fallback, legacy JSON settled guard, description
formatting, origin passthrough, already/settled outcomes).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.db.credit_ledger_client import ALREADY_APPLIED
from api.services.credits import reservation
from api.services.credits.reservation import (
    CREDITS_SETTLED_KEY,
    RESERVED_CREDIT_SECONDS_KEY,
    reconcile_call_credits,
    reserve_call_credits,
    reserve_call_credits_for_run,
    settle_workflow_run_credits,
)


def _patch(method, **kw):
    return patch.object(reservation.db_client, method, new=AsyncMock(**kw))


# ======== reserve_call_credits_for_run (run-scoped, single-tx) ========


async def test_reserve_for_run_unmetered_returns_zero_and_never_reserves():
    tx = AsyncMock(return_value=600)
    with _patch("get_free_call_seconds_remaining", return_value=None), patch.object(
        reservation.db_client, "reserve_run_credits_tx", new=tx
    ):
        assert await reserve_call_credits_for_run(1, 9, 600) == 0
    tx.assert_not_awaited()


async def test_reserve_for_run_metered_sufficient_returns_est():
    tx = AsyncMock(return_value=600)
    with _patch("get_free_call_seconds_remaining", return_value=1000), patch.object(
        reservation.db_client, "reserve_run_credits_tx", new=tx
    ):
        assert await reserve_call_credits_for_run(1, 9, 600) == 600
    tx.assert_awaited_once_with(1, 9, 600)


async def test_reserve_for_run_metered_insufficient_returns_none():
    with _patch("get_free_call_seconds_remaining", return_value=100), _patch(
        "reserve_run_credits_tx", return_value=None
    ):
        assert await reserve_call_credits_for_run(1, 9, 600) is None


async def test_reserve_for_run_retried_authorization_is_idempotent():
    """The idempotency key already exists → the hold is already in place."""
    with _patch("get_free_call_seconds_remaining", return_value=1000), _patch(
        "reserve_run_credits_tx", return_value=ALREADY_APPLIED
    ):
        assert await reserve_call_credits_for_run(1, 9, 600) == 600


async def test_reserve_for_run_zero_estimate_is_free():
    tx = AsyncMock()
    with _patch("get_free_call_seconds_remaining", return_value=1000), patch.object(
        reservation.db_client, "reserve_run_credits_tx", new=tx
    ):
        assert await reserve_call_credits_for_run(1, 9, 0) == 0
    tx.assert_not_awaited()


# ======== reserve_call_credits (legacy run-less) ========


async def test_reserve_unmetered_returns_zero_and_never_charges():
    charge = AsyncMock(return_value=True)
    with _patch("get_free_call_seconds_remaining", return_value=None), patch.object(
        reservation.db_client, "try_charge_call_seconds", new=charge
    ):
        assert await reserve_call_credits(1, 600) == 0
    charge.assert_not_awaited()


async def test_reserve_metered_sufficient_returns_est():
    with _patch("get_free_call_seconds_remaining", return_value=1000), _patch(
        "try_charge_call_seconds", return_value=True
    ):
        assert await reserve_call_credits(1, 600) == 600


async def test_reserve_metered_insufficient_returns_none():
    with _patch("get_free_call_seconds_remaining", return_value=100), _patch(
        "try_charge_call_seconds", return_value=False
    ):
        assert await reserve_call_credits(1, 600) is None


# ======== reconcile_call_credits (legacy) ========


async def test_reconcile_metered_releases_hold_then_charges_actual():
    add = AsyncMock(return_value=470)
    consume = AsyncMock()
    with _patch("get_free_call_seconds_remaining", return_value=400), patch.object(
        reservation.db_client, "add_call_seconds", new=add
    ), patch.object(reservation, "consume_free_call_seconds", new=consume):
        await reconcile_call_credits(1, 600, 130)
    add.assert_awaited_once_with(1, 600)
    consume.assert_awaited_once_with(1, 130)


async def test_reconcile_no_reservation_only_consumes():
    add = AsyncMock()
    consume = AsyncMock()
    with patch.object(reservation.db_client, "add_call_seconds", new=add), patch.object(
        reservation, "consume_free_call_seconds", new=consume
    ):
        await reconcile_call_credits(1, 0, 95)
    add.assert_not_awaited()
    consume.assert_awaited_once_with(1, 95)


async def test_reconcile_swallows_errors():
    with patch.object(
        reservation, "consume_free_call_seconds", new=AsyncMock(side_effect=RuntimeError("x"))
    ):
        await reconcile_call_credits(1, 0, 10)  # must not raise


# ======== settle_workflow_run_credits ========


def _run(**overrides):
    defaults = {
        "id": 9,
        "reserved_credit_seconds": None,
        "initial_context": {},
        "usage_info": {},
        "cost_info": {},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_settle_prefers_reserved_column_over_json():
    run = _run(
        reserved_credit_seconds=600,
        initial_context={RESERVED_CREDIT_SECONDS_KEY: 450, "called_number": "+9111"},
        usage_info={"call_duration_seconds": 130},
    )
    tx = AsyncMock(return_value="settled")
    with patch.object(reservation.db_client, "settle_run_credits_tx", new=tx):
        assert await settle_workflow_run_credits(1, run) == "settled"
    tx.assert_awaited_once_with(
        1, 9, 600, 130, origin="settle", description="Call to +9111 — 2m 10s"
    )


async def test_settle_falls_back_to_json_reserved_for_legacy_runs():
    run = _run(
        initial_context={RESERVED_CREDIT_SECONDS_KEY: 600},
        usage_info={"call_duration_seconds": 95},
    )
    tx = AsyncMock(return_value="settled")
    with patch.object(reservation.db_client, "settle_run_credits_tx", new=tx):
        await settle_workflow_run_credits(1, run)
    assert tx.await_args.args[:4] == (1, 9, 600, 95)


async def test_settle_skips_legacy_json_settled_runs():
    run = _run(
        initial_context={RESERVED_CREDIT_SECONDS_KEY: 600, CREDITS_SETTLED_KEY: True},
        usage_info={"call_duration_seconds": 130},
    )
    tx = AsyncMock()
    with patch.object(reservation.db_client, "settle_run_credits_tx", new=tx):
        assert await settle_workflow_run_credits(1, run) == ALREADY_APPLIED
    tx.assert_not_awaited()


async def test_settle_returns_already_from_tx_cas():
    """A retried settle loses the credits_settled_at CAS — no double-charge."""
    run = _run(reserved_credit_seconds=600, usage_info={"call_duration_seconds": 130})
    with _patch("settle_run_credits_tx", return_value=ALREADY_APPLIED):
        assert await settle_workflow_run_credits(1, run) == ALREADY_APPLIED


async def test_settle_duration_falls_back_to_cost_info():
    run = _run(
        reserved_credit_seconds=600,
        cost_info={"call_duration_seconds": 61},
    )
    tx = AsyncMock(return_value="settled")
    with patch.object(reservation.db_client, "settle_run_credits_tx", new=tx):
        await settle_workflow_run_credits(1, run)
    assert tx.await_args.args[3] == 61
    assert tx.await_args.kwargs["description"] == "Call to ? — 1m 01s"


async def test_settle_passes_origin_through():
    run = _run(reserved_credit_seconds=600)
    tx = AsyncMock(return_value="settled")
    with patch.object(reservation.db_client, "settle_run_credits_tx", new=tx):
        await settle_workflow_run_credits(1, run, origin="sweeper")
    assert tx.await_args.kwargs["origin"] == "sweeper"


async def test_settle_without_run_id_noops():
    run = _run(id=None, reserved_credit_seconds=600)
    tx = AsyncMock()
    with patch.object(reservation.db_client, "settle_run_credits_tx", new=tx):
        assert await settle_workflow_run_credits(1, run) == ALREADY_APPLIED
    tx.assert_not_awaited()
