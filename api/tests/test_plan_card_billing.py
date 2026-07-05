"""Plan-card billing: admin-designed monthly plan → PayU purchase → expiry
extension; expiry auto-suspends (and renewal auto-lifts)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes import billing
from api.services.admin import profile as profile_service
from api.services.admin.profile import plan_state


def _user(org=4):
    return SimpleNamespace(id=7, selected_organization_id=org, email="amit@x.test")


CARD = {
    "title": "Enterprise",
    "price_inr": 25000.0,
    "included_minutes": 3000,
    "features": ["Unlimited agents", "Priority support"],
    "enabled": True,
}


# ======== plan_state ========


def test_plan_state_no_card():
    s = plan_state({})
    assert s["enabled"] is False and s["expired"] is False and s["warn"] is False


def test_plan_state_card_never_purchased_not_expired():
    s = plan_state({"plan_card": CARD})
    assert s["enabled"] is True
    assert s["expired"] is False  # no expiry yet: wallet still gates calls
    assert s["days_left"] is None


def test_plan_state_warn_within_five_days():
    expires = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    s = plan_state({"plan_card": CARD, "plan_expires_at": expires})
    assert s["warn"] is True and s["expired"] is False


def test_plan_state_expired():
    expires = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    s = plan_state({"plan_card": CARD, "plan_expires_at": expires})
    assert s["expired"] is True and s["days_left"] == 0


# ======== auto-suspend ========


@pytest.mark.asyncio
async def test_expired_plan_suspends_and_renewal_lifts(monkeypatch):
    expired_profile = {
        "plan_card": CARD,
        "plan_expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
    }
    monkeypatch.setattr(
        profile_service,
        "get_admin_profile",
        AsyncMock(return_value=expired_profile),
    )
    assert await profile_service.is_org_suspended(4) is True

    renewed_profile = {
        "plan_card": CARD,
        "plan_expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
    }
    monkeypatch.setattr(
        profile_service,
        "get_admin_profile",
        AsyncMock(return_value=renewed_profile),
    )
    assert await profile_service.is_org_suspended(4) is False


@pytest.mark.asyncio
async def test_manual_suspend_still_wins(monkeypatch):
    monkeypatch.setattr(
        profile_service,
        "get_admin_profile",
        AsyncMock(return_value={"suspended": True}),
    )
    assert await profile_service.is_org_suspended(4) is True


# ======== extend_plan_month ========


@pytest.mark.asyncio
async def test_extend_from_now_when_lapsed(monkeypatch):
    profile = {
        "plan_card": dict(CARD),
        "plan_expires_at": (datetime.now(UTC) - timedelta(days=10)).isoformat(),
    }
    monkeypatch.setattr(
        profile_service, "get_admin_profile", AsyncMock(return_value=profile)
    )
    saved = AsyncMock(side_effect=lambda org, p: p)
    monkeypatch.setattr(profile_service, "_save_admin_profile", saved)

    new_expiry = await profile_service.extend_plan_month(4, txnid="t1")

    # Lapsed → extends from now, not from the old expiry.
    assert (new_expiry - datetime.now(UTC)).days in (29, 30)
    assert profile["plan_last_txnid"] == "t1"


@pytest.mark.asyncio
async def test_extend_early_renewal_stacks_on_current_expiry(monkeypatch):
    current = datetime.now(UTC) + timedelta(days=10)
    profile = {"plan_card": dict(CARD), "plan_expires_at": current.isoformat()}
    monkeypatch.setattr(
        profile_service, "get_admin_profile", AsyncMock(return_value=profile)
    )
    monkeypatch.setattr(
        profile_service, "_save_admin_profile", AsyncMock(side_effect=lambda o, p: p)
    )

    new_expiry = await profile_service.extend_plan_month(4, txnid="t2")

    assert (new_expiry - current).days == 30  # stacked, not reset


# ======== billing routes ========


@pytest.mark.asyncio
async def test_get_client_plan_returns_card_state(monkeypatch):
    expires = (datetime.now(UTC) + timedelta(days=4)).isoformat()
    monkeypatch.setattr(billing.payu_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        billing,
        "get_org_plan_state",
        AsyncMock(
            return_value=plan_state({"plan_card": CARD, "plan_expires_at": expires})
        ),
    )

    res = await billing.get_client_plan(user=_user())

    assert res["enabled"] is True
    assert res["title"] == "Enterprise"
    assert res["price_inr"] == 25000.0
    assert res["warn"] is True and res["expired"] is False


@pytest.mark.asyncio
async def test_plan_initiate_builds_payu_request(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "is_configured", lambda: True)
    monkeypatch.setattr(billing.payu_client, "payment_url", lambda: "https://payu/pay")
    captured = {}

    def _build(**kwargs):
        captured.update(kwargs)
        return {"key": "k", "hash": "h", **kwargs}

    monkeypatch.setattr(billing.payu_client, "build_payment_params", _build)
    monkeypatch.setattr(
        billing, "get_org_plan_state", AsyncMock(return_value=plan_state({"plan_card": CARD}))
    )
    monkeypatch.setattr(
        billing, "get_backend_endpoints", AsyncMock(return_value=("https://api.x", None))
    )
    create_txn = AsyncMock()
    with patch.object(billing.db_client, "create_transaction", new=create_txn):
        res = await billing.plan_initiate(user=_user())

    assert res["payment_url"] == "https://payu/pay"
    assert captured["amount"] == "25000.00"
    assert captured["udf2"] == "plan"
    txn_kwargs = create_txn.await_args.kwargs
    assert txn_kwargs["pack_id"] == "plan"
    assert txn_kwargs["seconds"] == 3000 * 60
    assert txn_kwargs["amount_paise"] == 2500000


@pytest.mark.asyncio
async def test_plan_initiate_400_without_card(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        billing, "get_org_plan_state", AsyncMock(return_value=plan_state({}))
    )
    with pytest.raises(HTTPException) as exc:
        await billing.plan_initiate(user=_user())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_callback_plan_purchase_extends_expiry_once(monkeypatch):
    """A successful plan payment credits minutes AND extends expiry; the
    duplicate (webhook after callback) does neither."""
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: True)
    txn = SimpleNamespace(
        organization_id=4, status="created", pack_id="plan",
        seconds=180000, amount_paise=2500000,
    )
    extend = AsyncMock(return_value=datetime.now(UTC) + timedelta(days=30))
    monkeypatch.setattr(billing, "extend_plan_month", extend)
    params = {"txnid": "a4yplanx", "status": "success", "amount": "25000.00", "mihpayid": "m1"}

    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock(return_value="credited")),
    ):
        outcome = await billing._apply_payu_payment(params)
    assert outcome == "success"
    extend.assert_awaited_once_with(4, txnid="a4yplanx")

    # Duplicate: paid-CAS reports "already" → no second extension.
    extend.reset_mock()
    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock(return_value="already")),
    ):
        outcome2 = await billing._apply_payu_payment(params)
    assert outcome2 == "already"
    extend.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_pack_purchase_never_touches_plan(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: True)
    txn = SimpleNamespace(
        organization_id=4, status="created", pack_id="growth",
        seconds=39000, amount_paise=450000,
    )
    extend = AsyncMock()
    monkeypatch.setattr(billing, "extend_plan_month", extend)

    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        patch.object(billing.db_client, "topup_paid_tx", new=AsyncMock(return_value="credited")),
    ):
        outcome = await billing._apply_payu_payment(
            {"txnid": "a4yx", "status": "success", "amount": "4500.00", "mihpayid": "m2"}
        )
    assert outcome == "success"
    extend.assert_not_awaited()
