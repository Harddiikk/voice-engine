"""Billing money-core routes: /ledger paging + org scoping, /balance on-hold,
and /verify's atomic topup outcomes (credited / already / unmetered)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routes import billing


def _user(org=4):
    return SimpleNamespace(id=7, selected_organization_id=org, email="amit@x.test")


def _entry(**overrides):
    defaults = {
        "id": 1,
        "kind": "settle_charge",
        "delta_seconds": -130,
        "balance_after": 470,
        "workflow_run_id": 9,
        "description": "Call to +9111 — 2m 10s",
        "created_at": "2026-07-01T00:00:00Z",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ======== GET /billing/ledger ========


@pytest.mark.asyncio
async def test_ledger_returns_org_scoped_entries():
    entries = [_entry(id=2, kind="topup", delta_seconds=18000), _entry(id=1)]
    with patch.object(
        billing.db_client, "list_ledger_entries", new=AsyncMock(return_value=entries)
    ) as lst:
        rows = await billing.list_ledger(user=_user(org=4))
    lst.assert_awaited_once_with(4, limit=50, offset=0, kind=None)
    assert [r["id"] for r in rows] == [2, 1]
    assert rows[0]["kind"] == "topup"
    assert rows[1]["delta_seconds"] == -130
    assert rows[1]["balance_after"] == 470
    assert rows[1]["workflow_run_id"] == 9


@pytest.mark.asyncio
async def test_ledger_clamps_limit_and_offset_and_passes_kind():
    with patch.object(
        billing.db_client, "list_ledger_entries", new=AsyncMock(return_value=[])
    ) as lst:
        await billing.list_ledger(user=_user(), limit=5000, offset=-3, kind="topup")
    lst.assert_awaited_once_with(4, limit=200, offset=0, kind="topup")


@pytest.mark.asyncio
async def test_ledger_requires_selected_org():
    with pytest.raises(billing.HTTPException) as exc:
        await billing.list_ledger(user=_user(org=None))
    assert exc.value.status_code == 400


# ======== GET /billing/balance (on_hold_seconds) ========


@pytest.mark.asyncio
async def test_balance_includes_on_hold_seconds(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "is_configured", lambda: False)
    monkeypatch.setattr(billing.razorpay_client, "is_configured", lambda: False)
    with (
        patch.object(
            billing.db_client,
            "get_free_call_seconds_remaining",
            new=AsyncMock(return_value=1200),
        ),
        patch.object(
            billing.db_client, "sum_on_hold_seconds", new=AsyncMock(return_value=600)
        ) as on_hold,
        patch.object(billing, "get_org_plan", new=AsyncMock(return_value=None)),
    ):
        body = await billing.get_balance(user=_user(org=4))
    on_hold.assert_awaited_once_with(4)
    assert body["balance_seconds"] == 1200
    assert body["on_hold_seconds"] == 600
    assert body["unlimited"] is False


# ======== POST /billing/verify (atomic topup) ========


def _verify_body():
    return billing.VerifyRequest(
        razorpay_order_id="order_1",
        razorpay_payment_id="pay_1",
        razorpay_signature="sig",
    )


@pytest.mark.asyncio
async def test_verify_credits_via_topup_tx(monkeypatch):
    monkeypatch.setattr(
        billing.razorpay_client, "verify_payment_signature", lambda **kw: True
    )
    txn = SimpleNamespace(status="created", seconds=18000)
    with (
        patch.object(
            billing.db_client,
            "get_transaction_by_order_id",
            new=AsyncMock(return_value=txn),
        ),
        patch.object(
            billing.db_client, "topup_paid_tx", new=AsyncMock(return_value="credited")
        ) as topup,
        patch.object(
            billing.db_client,
            "get_free_call_seconds_remaining",
            new=AsyncMock(return_value=19800),
        ),
    ):
        body = await billing.verify_payment(_verify_body(), user=_user(org=4))
    topup.assert_awaited_once_with("order_1", "pay_1")
    assert body == {"ok": True, "balance_seconds": 19800}


@pytest.mark.asyncio
async def test_verify_already_paid_short_circuits_without_tx(monkeypatch):
    txn = SimpleNamespace(status="paid", seconds=18000)
    with (
        patch.object(
            billing.db_client,
            "get_transaction_by_order_id",
            new=AsyncMock(return_value=txn),
        ),
        patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock()) as topup,
        patch.object(
            billing.db_client,
            "get_free_call_seconds_remaining",
            new=AsyncMock(return_value=19800),
        ),
    ):
        body = await billing.verify_payment(_verify_body(), user=_user(org=4))
    topup.assert_not_awaited()
    assert body["already"] is True


@pytest.mark.asyncio
async def test_verify_concurrent_topup_reports_already(monkeypatch):
    """The tx CAS lost against a concurrent verify — no double-credit."""
    monkeypatch.setattr(
        billing.razorpay_client, "verify_payment_signature", lambda **kw: True
    )
    txn = SimpleNamespace(status="created", seconds=18000)
    with (
        patch.object(
            billing.db_client,
            "get_transaction_by_order_id",
            new=AsyncMock(return_value=txn),
        ),
        patch.object(
            billing.db_client, "topup_paid_tx", new=AsyncMock(return_value="already")
        ),
        patch.object(
            billing.db_client,
            "get_free_call_seconds_remaining",
            new=AsyncMock(return_value=19800),
        ),
    ):
        body = await billing.verify_payment(_verify_body(), user=_user(org=4))
    assert body == {"ok": True, "balance_seconds": 19800, "already": True}


@pytest.mark.asyncio
async def test_verify_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(
        billing.razorpay_client, "verify_payment_signature", lambda **kw: False
    )
    txn = SimpleNamespace(status="created", seconds=18000)
    with (
        patch.object(
            billing.db_client,
            "get_transaction_by_order_id",
            new=AsyncMock(return_value=txn),
        ),
        patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock()) as topup,
    ):
        with pytest.raises(billing.HTTPException) as exc:
            await billing.verify_payment(_verify_body(), user=_user(org=4))
    assert exc.value.status_code == 400
    topup.assert_not_awaited()
