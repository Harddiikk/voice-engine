"""PayU billing routes: initiate builds a signed request + a server-side txn;
callback credits only on a verified, successful, correct-amount response."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routes import billing


class _Req:
    def __init__(self, data, json_body=None, content_type="application/x-www-form-urlencoded"):
        self._data = data
        self._json = json_body
        self.headers = {"content-type": content_type}

    async def form(self):
        return self._data

    async def json(self):
        return self._json


def _user(org=4):
    return SimpleNamespace(id=7, selected_organization_id=org, email="amit@x.test")


@pytest.mark.asyncio
async def test_balance_carries_money_fields(monkeypatch):
    """GET /balance surfaces the ₹ money view alongside the existing fields."""
    monkeypatch.setattr(billing.payu_client, "is_configured", lambda: True)
    monkeypatch.setattr(billing.razorpay_client, "is_configured", lambda: False)
    monkeypatch.setattr(billing, "get_org_plan", AsyncMock(return_value="starter"))
    monkeypatch.setattr(billing, "features_for_plan", lambda plan: {"api": True, "mcp": False})
    monkeypatch.setattr(
        billing,
        "get_org_money",
        AsyncMock(
            return_value={
                "balance_seconds": 120000,
                "unlimited": False,
                "per_minute_inr": 8.0,
                "money_left_inr": 16000.0,
                "spent_seconds": 23610,
                "money_spent_inr": 3148.0,
                "spent_today_seconds": 0,
                "money_spent_today_inr": 0.0,
            }
        ),
    )
    with (
        patch.object(billing.db_client, "get_free_call_seconds_remaining", new=AsyncMock(return_value=120000)),
        patch.object(billing.db_client, "sum_on_hold_seconds", new=AsyncMock(return_value=0)),
    ):
        res = await billing.get_balance(user=_user())

    assert res["per_minute_inr"] == 8.0
    assert res["money_left_inr"] == 16000.0
    assert res["money_spent_inr"] == 3148.0
    # Existing fields still present.
    assert res["balance_seconds"] == 120000
    assert res["unlimited"] is False


@pytest.mark.asyncio
async def test_balance_money_left_none_when_unlimited(monkeypatch):
    """Unlimited orgs report no ₹-remaining (None) but still expose rate + spend."""
    monkeypatch.setattr(billing.payu_client, "is_configured", lambda: True)
    monkeypatch.setattr(billing.razorpay_client, "is_configured", lambda: False)
    monkeypatch.setattr(billing, "get_org_plan", AsyncMock(return_value="trial"))
    monkeypatch.setattr(billing, "features_for_plan", lambda plan: {"api": False, "mcp": False})
    monkeypatch.setattr(
        billing,
        "get_org_money",
        AsyncMock(
            return_value={
                "balance_seconds": None,
                "unlimited": True,
                "per_minute_inr": 8.0,
                "money_left_inr": None,
                "spent_seconds": 0,
                "money_spent_inr": 0.0,
                "spent_today_seconds": 0,
                "money_spent_today_inr": 0.0,
            }
        ),
    )
    with (
        patch.object(billing.db_client, "get_free_call_seconds_remaining", new=AsyncMock(return_value=None)),
        patch.object(billing.db_client, "sum_on_hold_seconds", new=AsyncMock(return_value=0)),
    ):
        res = await billing.get_balance(user=_user())

    assert res["unlimited"] is True
    assert res["money_left_inr"] is None
    assert res["per_minute_inr"] == 8.0
    assert res["money_spent_inr"] == 0.0


@pytest.mark.asyncio
async def test_payu_initiate_creates_txn_and_returns_signed_params(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        billing.payu_client, "payment_url", lambda: "https://test.payu.in/_payment"
    )
    monkeypatch.setattr(
        billing.payu_client,
        "build_payment_params",
        lambda **kw: {"key": "XxWLV8", "txnid": kw["txnid"], "hash": "h", "amount": kw["amount"]},
    )
    with (
        patch.object(billing.db_client, "get_free_call_seconds_remaining", new=AsyncMock(return_value=1800)),
        patch.object(billing.db_client, "create_transaction", new=AsyncMock()) as create,
        patch.object(billing, "get_backend_endpoints", new=AsyncMock(return_value=("https://api.auto4you.in", "m"))),
    ):
        res = await billing.payu_initiate(
            billing.CreateOrderRequest(pack_id="starter"), user=_user()
        )

    assert res["payment_url"] == "https://test.payu.in/_payment"
    assert res["params"]["key"] == "XxWLV8"
    assert res["params"]["amount"] == "2399.00"
    kw = create.await_args.kwargs
    assert kw["pack_id"] == "starter"
    assert kw["amount_paise"] == 239900
    assert kw["seconds"] == 300 * 60
    assert kw["razorpay_order_id"].startswith("a4y")  # PayU txnid in the gateway slot


@pytest.mark.asyncio
async def test_payu_initiate_rejects_unlimited_org(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "is_configured", lambda: True)
    with patch.object(
        billing.db_client, "get_free_call_seconds_remaining", new=AsyncMock(return_value=None)
    ):
        with pytest.raises(billing.HTTPException) as exc:
            await billing.payu_initiate(
                billing.CreateOrderRequest(pack_id="starter"), user=_user()
            )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_payu_callback_credits_on_verified_success(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: True)
    monkeypatch.setattr(billing, "UI_APP_URL", "https://app.auto4you.in")
    txn = SimpleNamespace(status="created", organization_id=4, seconds=18000, amount_paise=239900)
    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock(return_value="credited")) as topup,
    ):
        resp = await billing.payu_callback(
            _Req({"status": "success", "txnid": "a4yX", "amount": "2399.00", "mihpayid": "MP1", "hash": "h"})
        )
    assert resp.status_code == 303
    assert "payment=success" in resp.headers["location"]
    topup.assert_awaited_once_with("a4yX", "MP1")


@pytest.mark.asyncio
async def test_payu_callback_skips_credit_for_unlimited_org(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: True)
    monkeypatch.setattr(billing, "UI_APP_URL", "https://app.auto4you.in")
    txn = SimpleNamespace(status="created", organization_id=1, seconds=60, amount_paise=3000)
    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        # tx marks the payment paid but refuses to meter an unlimited org
        patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock(return_value="unmetered")) as topup,
    ):
        resp = await billing.payu_callback(
            _Req({"status": "success", "txnid": "a4ytestX", "amount": "30.00", "hash": "h"})
        )
    assert "payment=success" in resp.headers["location"]
    topup.assert_awaited_once()         # payment recorded (marked paid, not credited)


@pytest.mark.asyncio
async def test_payu_test_initiate_builds_30rs_request(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "is_configured", lambda: True)
    monkeypatch.setattr(billing.payu_client, "payment_url", lambda: "https://secure.payu.in/_payment")
    monkeypatch.setattr(
        billing.payu_client, "build_payment_params",
        lambda **kw: {"amount": kw["amount"], "productinfo": kw["productinfo"], "txnid": kw["txnid"]},
    )
    with (
        patch.object(billing.db_client, "create_transaction", new=AsyncMock()) as create,
        patch.object(billing, "get_backend_endpoints", new=AsyncMock(return_value=("https://api.auto4you.in", "m"))),
    ):
        res = await billing.payu_test_initiate(user=_user())
    assert res["payment_url"] == "https://secure.payu.in/_payment"
    assert res["params"]["amount"] == "30.00"
    assert res["params"]["productinfo"] == "PayU Test Rs 30"
    kw = create.await_args.kwargs
    assert kw["amount_paise"] == 3000
    assert kw["seconds"] == 60
    assert kw["pack_id"] == "test"
    assert kw["razorpay_order_id"].startswith("a4ytest")


@pytest.mark.asyncio
async def test_payu_callback_rejects_bad_hash(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: False)
    monkeypatch.setattr(billing, "UI_APP_URL", "https://app.auto4you.in")
    with patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock()) as topup:
        resp = await billing.payu_callback(
            _Req({"status": "success", "txnid": "a4yX", "amount": "2399.00", "hash": "forged"})
        )
    assert resp.status_code == 303
    assert "payment=failed" in resp.headers["location"]
    topup.assert_not_awaited()


@pytest.mark.asyncio
async def test_payu_callback_idempotent_when_already_paid(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: True)
    monkeypatch.setattr(billing, "UI_APP_URL", "https://app.auto4you.in")
    txn = SimpleNamespace(status="paid", organization_id=4, seconds=18000, amount_paise=239900)
    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock()) as topup,
    ):
        resp = await billing.payu_callback(
            _Req({"status": "success", "txnid": "a4yX", "amount": "2399.00", "hash": "h"})
        )
    assert "payment=success" in resp.headers["location"]
    topup.assert_not_awaited()


@pytest.mark.asyncio
async def test_payu_callback_idempotent_when_topup_cas_lost(monkeypatch):
    """Race: another delivery marked it paid between our read and the tx."""
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: True)
    monkeypatch.setattr(billing, "UI_APP_URL", "https://app.auto4you.in")
    txn = SimpleNamespace(status="created", organization_id=4, seconds=18000, amount_paise=239900)
    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock(return_value="already")),
    ):
        resp = await billing.payu_callback(
            _Req({"status": "success", "txnid": "a4yX", "amount": "2399.00", "hash": "h"})
        )
    assert "payment=success" in resp.headers["location"]


@pytest.mark.asyncio
async def test_payu_callback_rejects_amount_mismatch(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: True)
    monkeypatch.setattr(billing, "UI_APP_URL", "https://app.auto4you.in")
    txn = SimpleNamespace(status="created", organization_id=4, seconds=18000, amount_paise=239900)
    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock()) as topup,
    ):
        resp = await billing.payu_callback(
            _Req({"status": "success", "txnid": "a4yX", "amount": "1.00", "hash": "h"})
        )
    assert "payment=failed" in resp.headers["location"]
    topup.assert_not_awaited()


@pytest.mark.asyncio
async def test_payu_webhook_credits_idempotently(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: True)
    txn = SimpleNamespace(status="created", organization_id=4, seconds=18000, amount_paise=239900)
    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock(return_value="credited")) as topup,
    ):
        resp = await billing.payu_webhook(
            _Req({"status": "success", "txnid": "a4yX", "amount": "2399.00", "mihpayid": "MP", "hash": "h"})
        )
    assert resp == {"ok": True, "outcome": "success"}
    topup.assert_awaited_once_with("a4yX", "MP")


@pytest.mark.asyncio
async def test_payu_webhook_rejects_bad_hash(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: False)
    with patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock()) as topup:
        resp = await billing.payu_webhook(
            _Req({"status": "success", "txnid": "x", "amount": "1", "hash": "bad"})
        )
    assert resp["ok"] is False
    topup.assert_not_awaited()
