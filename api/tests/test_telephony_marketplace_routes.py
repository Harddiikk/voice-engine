"""Marketplace buy route: sequencing (suspend gate -> provision -> fail-closed KYC
gate -> pool check -> per-client charge -> assign -> local bookkeeping), per-client
price fields, atomic charge, refund on assign failure, unmetered skip, suspend 403.
(Service-level tests live in test_telephony_marketplace.py.)"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes import telephony_marketplace as tm
from api.services.voicelink_clients.client import VoiceLinkClientError

# Per-client pricing distinct from the global defaults (500 INR @ ₹8/min -> 3750s)
# so the tests actually prove the per-client path is used: ₹600 @ ₹10/min -> 3600s.
PRICE_INR = 600
SETUP_SECONDS = 3600
PRICING = {
    "per_minute_inr": 10.0,
    "number_price_inr": PRICE_INR,
    "setup_fee_inr": 0,
    "custom": {},
}


def _user(org=4):
    return SimpleNamespace(id=7, selected_organization_id=org)


def _org_row(client_id="474"):
    return SimpleNamespace(voicelink_client_id=client_id, voicelink_username="jane.4")


AVAILABLE = [{"did_id": 942, "did_number": "9484959244", "user_status": 1}]


def _gates():
    """No-op patches for the buy flow's gates + per-client pricing. Tests pick the
    ones they need to enter (and override individually when asserting a gate)."""
    return {
        "suspend": patch.object(tm, "assert_org_not_suspended", new=AsyncMock()),
        "kyc": patch.object(
            tm, "assert_org_kyc_complete_for_purchase", new=AsyncMock()
        ),
        "ensure": patch.object(tm, "ensure_voicelink_client", new=AsyncMock()),
        "pricing": patch.object(
            tm, "get_org_pricing", new=AsyncMock(return_value=PRICING)
        ),
    }


# ======== GET /numbers ========


@pytest.mark.asyncio
async def test_numbers_reports_per_client_price_and_setup_seconds():
    with (
        patch.object(tm, "get_org_pricing", new=AsyncMock(return_value=PRICING)),
        patch.object(
            tm.mkt, "list_available_numbers", new=AsyncMock(return_value=AVAILABLE)
        ),
    ):
        body = await tm.available_numbers(user=_user())
    assert body["numbers"] == AVAILABLE
    assert body["price_inr"] == PRICE_INR
    assert body["setup_seconds"] == SETUP_SECONDS


# ======== GET /my-numbers ========


@pytest.mark.asyncio
async def test_my_numbers_delegates_to_resolved_listing():
    resolved = [{"did_number": "919484959244", "source": "local"}]
    with patch.object(
        tm.mkt, "list_org_numbers_resolved", new=AsyncMock(return_value=resolved)
    ) as listing:
        body = await tm.my_numbers(user=_user())
    listing.assert_awaited_once_with(4)
    assert body == {"numbers": resolved}


# ======== POST /buy ========


@pytest.mark.asyncio
async def test_buy_charges_per_client_then_assigns_then_records():
    g = _gates()
    with (
        g["suspend"],
        g["kyc"],
        g["ensure"],
        g["pricing"],
        patch.object(
            tm.db_client,
            "get_organization_by_id",
            new=AsyncMock(return_value=_org_row()),
        ),
        patch.object(
            tm.mkt, "list_available_numbers", new=AsyncMock(return_value=AVAILABLE)
        ),
        patch.object(
            tm.db_client, "charge_purchase_tx", new=AsyncMock(return_value=26250)
        ) as charge,
        patch.object(tm.mkt, "assign_number", new=AsyncMock()) as assign,
        patch.object(tm.mkt, "record_number_purchase", new=AsyncMock()) as record,
        patch.object(tm.db_client, "refund_tx", new=AsyncMock()) as refund,
        patch.object(
            tm.db_client,
            "get_free_call_seconds_remaining",
            new=AsyncMock(return_value=26250),
        ),
    ):
        body = await tm.buy_number(tm.BuyNumberRequest(did_id=942), user=_user())

    charge.assert_awaited_once_with(
        4,
        SETUP_SECONDS,
        kind="number_purchase",
        description=f"Phone number 9484959244 — ₹{PRICE_INR}",
    )
    assign.assert_awaited_once_with("474", 942)
    record.assert_awaited_once_with(
        4, client_id="474", did_number="9484959244", username="jane.4"
    )
    refund.assert_not_awaited()
    assert body == {
        "ok": True,
        "did_id": 942,
        "did_number": "9484959244",
        "balance_seconds": 26250,
    }


@pytest.mark.asyncio
async def test_buy_suspends_before_provision_gate_and_charge():
    order = []
    suspend = AsyncMock(side_effect=lambda *a, **k: order.append("suspend"))
    ensure = AsyncMock(side_effect=lambda *a, **k: order.append("ensure"))
    gate = AsyncMock(side_effect=lambda *a, **k: order.append("gate"))
    charge = AsyncMock(side_effect=lambda *a, **k: order.append("charge") or 26250)
    with (
        patch.object(tm, "assert_org_not_suspended", new=suspend),
        patch.object(tm, "ensure_voicelink_client", new=ensure),
        patch.object(tm, "assert_org_kyc_complete_for_purchase", new=gate),
        patch.object(
            tm, "get_org_pricing", new=AsyncMock(return_value=PRICING)
        ),
        patch.object(
            tm.db_client,
            "get_organization_by_id",
            new=AsyncMock(return_value=_org_row()),
        ),
        patch.object(
            tm.mkt, "list_available_numbers", new=AsyncMock(return_value=AVAILABLE)
        ),
        patch.object(tm.db_client, "charge_purchase_tx", new=charge),
        patch.object(tm.mkt, "assign_number", new=AsyncMock()),
        patch.object(tm.mkt, "record_number_purchase", new=AsyncMock()),
        patch.object(
            tm.db_client,
            "get_free_call_seconds_remaining",
            new=AsyncMock(return_value=26250),
        ),
    ):
        await tm.buy_number(tm.BuyNumberRequest(did_id=942), user=_user())

    assert order == ["suspend", "ensure", "gate", "charge"]
    suspend.assert_awaited_once_with(4)


@pytest.mark.asyncio
async def test_buy_403_when_suspended_never_provisions_or_charges():
    with (
        patch.object(
            tm,
            "assert_org_not_suspended",
            new=AsyncMock(
                side_effect=HTTPException(
                    status_code=403, detail={"code": "account_suspended"}
                )
            ),
        ),
        patch.object(tm, "ensure_voicelink_client", new=AsyncMock()) as ensure,
        patch.object(tm.db_client, "charge_purchase_tx", new=AsyncMock()) as charge,
    ):
        with pytest.raises(HTTPException) as exc:
            await tm.buy_number(tm.BuyNumberRequest(did_id=942), user=_user())
    assert exc.value.status_code == 403
    ensure.assert_not_awaited()  # suspended: don't even provision
    charge.assert_not_awaited()


@pytest.mark.asyncio
async def test_buy_400_when_still_unprovisioned_after_ensure():
    g = _gates()
    with (
        g["suspend"],
        g["ensure"],
        patch.object(
            tm.db_client,
            "get_organization_by_id",
            new=AsyncMock(return_value=_org_row(client_id=None)),
        ),
        patch.object(
            tm, "assert_org_kyc_complete_for_purchase", new=AsyncMock()
        ) as gate,
        patch.object(tm.db_client, "charge_purchase_tx", new=AsyncMock()) as charge,
    ):
        with pytest.raises(tm.HTTPException) as exc:
            await tm.buy_number(tm.BuyNumberRequest(did_id=942), user=_user())
    assert exc.value.status_code == 400
    assert exc.value.detail == "telephony_account_not_provisioned"
    gate.assert_not_awaited()
    charge.assert_not_awaited()


@pytest.mark.asyncio
async def test_buy_403_kyc_block_happens_before_charge():
    g = _gates()
    with (
        g["suspend"],
        g["ensure"],
        patch.object(
            tm.db_client,
            "get_organization_by_id",
            new=AsyncMock(return_value=_org_row()),
        ),
        patch.object(
            tm,
            "assert_org_kyc_complete_for_purchase",
            new=AsyncMock(side_effect=HTTPException(status_code=403, detail="kyc")),
        ),
        patch.object(tm.db_client, "charge_purchase_tx", new=AsyncMock()) as charge,
        patch.object(tm.mkt, "assign_number", new=AsyncMock()) as assign,
    ):
        with pytest.raises(tm.HTTPException) as exc:
            await tm.buy_number(tm.BuyNumberRequest(did_id=942), user=_user())
    assert exc.value.status_code == 403
    charge.assert_not_awaited()
    assign.assert_not_awaited()


@pytest.mark.asyncio
async def test_buy_402_when_insufficient_and_never_assigns():
    g = _gates()
    with (
        g["suspend"],
        g["kyc"],
        g["ensure"],
        g["pricing"],
        patch.object(
            tm.db_client,
            "get_organization_by_id",
            new=AsyncMock(return_value=_org_row()),
        ),
        patch.object(
            tm.mkt, "list_available_numbers", new=AsyncMock(return_value=AVAILABLE)
        ),
        patch.object(
            tm.db_client, "charge_purchase_tx", new=AsyncMock(return_value=None)
        ),
        patch.object(tm.mkt, "assign_number", new=AsyncMock()) as assign,
    ):
        with pytest.raises(tm.HTTPException) as exc:
            await tm.buy_number(tm.BuyNumberRequest(did_id=942), user=_user())
    assert exc.value.status_code == 402
    assign.assert_not_awaited()


@pytest.mark.asyncio
async def test_buy_refunds_per_client_amount_when_assign_fails():
    g = _gates()
    with (
        g["suspend"],
        g["kyc"],
        g["ensure"],
        g["pricing"],
        patch.object(
            tm.db_client,
            "get_organization_by_id",
            new=AsyncMock(return_value=_org_row()),
        ),
        patch.object(
            tm.mkt, "list_available_numbers", new=AsyncMock(return_value=AVAILABLE)
        ),
        patch.object(
            tm.db_client, "charge_purchase_tx", new=AsyncMock(return_value=26250)
        ),
        patch.object(
            tm.mkt,
            "assign_number",
            new=AsyncMock(side_effect=VoiceLinkClientError("map failed")),
        ),
        patch.object(tm.mkt, "record_number_purchase", new=AsyncMock()) as record,
        patch.object(tm.db_client, "refund_tx", new=AsyncMock()) as refund,
    ):
        with pytest.raises(tm.HTTPException) as exc:
            await tm.buy_number(tm.BuyNumberRequest(did_id=942), user=_user())
    assert exc.value.status_code == 502
    refund.assert_awaited_once_with(
        4,
        SETUP_SECONDS,
        description="Refund: phone number 9484959244 assignment failed",
    )
    record.assert_not_awaited()  # nothing owned -> nothing to record


@pytest.mark.asyncio
async def test_buy_bookkeeping_failure_after_map_never_refunds_or_raises():
    """Once map_did succeeded the org owns the DID: a local persist failure is
    logged loudly but must not refund the charge or fail the purchase."""
    g = _gates()
    with (
        g["suspend"],
        g["kyc"],
        g["ensure"],
        g["pricing"],
        patch.object(
            tm.db_client,
            "get_organization_by_id",
            new=AsyncMock(return_value=_org_row()),
        ),
        patch.object(
            tm.mkt, "list_available_numbers", new=AsyncMock(return_value=AVAILABLE)
        ),
        patch.object(
            tm.db_client, "charge_purchase_tx", new=AsyncMock(return_value=26250)
        ),
        patch.object(tm.mkt, "assign_number", new=AsyncMock()),
        patch.object(
            tm.mkt,
            "record_number_purchase",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ),
        patch.object(tm.db_client, "refund_tx", new=AsyncMock()) as refund,
        patch.object(
            tm.db_client,
            "get_free_call_seconds_remaining",
            new=AsyncMock(return_value=26250),
        ),
    ):
        body = await tm.buy_number(tm.BuyNumberRequest(did_id=942), user=_user())

    refund.assert_not_awaited()
    assert body["ok"] is True
    assert body["did_number"] == "9484959244"


@pytest.mark.asyncio
async def test_buy_unmetered_org_is_never_charged_or_refunded():
    g = _gates()
    with (
        g["suspend"],
        g["kyc"],
        g["ensure"],
        g["pricing"],
        patch.object(
            tm.db_client,
            "get_organization_by_id",
            new=AsyncMock(return_value=_org_row()),
        ),
        patch.object(
            tm.mkt, "list_available_numbers", new=AsyncMock(return_value=AVAILABLE)
        ),
        patch.object(
            tm.db_client,
            "charge_purchase_tx",
            new=AsyncMock(return_value="unmetered"),
        ),
        patch.object(
            tm.mkt,
            "assign_number",
            new=AsyncMock(side_effect=VoiceLinkClientError("map failed")),
        ),
        patch.object(tm.db_client, "refund_tx", new=AsyncMock()) as refund,
    ):
        with pytest.raises(tm.HTTPException) as exc:
            await tm.buy_number(tm.BuyNumberRequest(did_id=942), user=_user())
    assert exc.value.status_code == 502
    refund.assert_not_awaited()  # nothing was charged, nothing to refund


@pytest.mark.asyncio
async def test_buy_409_when_did_not_in_available_pool():
    g = _gates()
    with (
        g["suspend"],
        g["kyc"],
        g["ensure"],
        g["pricing"],
        patch.object(
            tm.db_client,
            "get_organization_by_id",
            new=AsyncMock(return_value=_org_row()),
        ),
        patch.object(
            tm.mkt, "list_available_numbers", new=AsyncMock(return_value=AVAILABLE)
        ),
        patch.object(tm.db_client, "charge_purchase_tx", new=AsyncMock()) as charge,
    ):
        with pytest.raises(tm.HTTPException) as exc:
            await tm.buy_number(tm.BuyNumberRequest(did_id=111), user=_user())
    assert exc.value.status_code == 409
    charge.assert_not_awaited()
