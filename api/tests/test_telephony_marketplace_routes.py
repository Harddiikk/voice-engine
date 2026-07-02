"""Marketplace buy route money paths: price fields, atomic charge, refund on
assign failure, unmetered skip. (Service-level tests live in
test_telephony_marketplace.py.)"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routes import telephony_marketplace as tm
from api.services.voicelink_clients.client import VoiceLinkClientError


def _user(org=4):
    return SimpleNamespace(id=7, selected_organization_id=org)


def _org_row(client_id="474"):
    return SimpleNamespace(voicelink_client_id=client_id)


AVAILABLE = [{"did_id": 942, "did_number": "9484959244", "user_status": 1}]


def _gates():
    return (
        patch.object(tm, "assert_org_kyc_complete", new=AsyncMock()),
        patch.object(tm, "ensure_voicelink_client", new=AsyncMock()),
    )


# ======== GET /numbers ========


@pytest.mark.asyncio
async def test_numbers_reports_price_and_setup_seconds():
    with patch.object(
        tm.mkt, "list_available_numbers", new=AsyncMock(return_value=AVAILABLE)
    ):
        body = await tm.available_numbers(user=_user())
    assert body["numbers"] == AVAILABLE
    assert body["price_inr"] == tm.NUMBER_PRICE_INR
    assert body["setup_seconds"] == tm.NUMBER_SETUP_SECONDS


# ======== POST /buy ========


@pytest.mark.asyncio
async def test_buy_charges_then_assigns():
    kyc, ensure = _gates()
    with (
        kyc,
        ensure,
        patch.object(
            tm.db_client, "get_organization_by_id", new=AsyncMock(return_value=_org_row())
        ),
        patch.object(
            tm.mkt, "list_available_numbers", new=AsyncMock(return_value=AVAILABLE)
        ),
        patch.object(
            tm.db_client, "charge_purchase_tx", new=AsyncMock(return_value=26250)
        ) as charge,
        patch.object(tm.mkt, "assign_number", new=AsyncMock()) as assign,
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
        tm.NUMBER_SETUP_SECONDS,
        kind="number_purchase",
        description=f"Phone number 9484959244 — ₹{tm.NUMBER_PRICE_INR}",
    )
    assign.assert_awaited_once_with("474", 942)
    refund.assert_not_awaited()
    assert body == {"ok": True, "did_id": 942, "balance_seconds": 26250}


@pytest.mark.asyncio
async def test_buy_402_when_insufficient_and_never_assigns():
    kyc, ensure = _gates()
    with (
        kyc,
        ensure,
        patch.object(
            tm.db_client, "get_organization_by_id", new=AsyncMock(return_value=_org_row())
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
async def test_buy_refunds_when_assign_fails():
    kyc, ensure = _gates()
    with (
        kyc,
        ensure,
        patch.object(
            tm.db_client, "get_organization_by_id", new=AsyncMock(return_value=_org_row())
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
        patch.object(tm.db_client, "refund_tx", new=AsyncMock()) as refund,
    ):
        with pytest.raises(tm.HTTPException) as exc:
            await tm.buy_number(tm.BuyNumberRequest(did_id=942), user=_user())
    assert exc.value.status_code == 502
    refund.assert_awaited_once_with(
        4,
        tm.NUMBER_SETUP_SECONDS,
        description="Refund: phone number 9484959244 assignment failed",
    )


@pytest.mark.asyncio
async def test_buy_unmetered_org_is_never_charged_or_refunded():
    kyc, ensure = _gates()
    with (
        kyc,
        ensure,
        patch.object(
            tm.db_client, "get_organization_by_id", new=AsyncMock(return_value=_org_row())
        ),
        patch.object(
            tm.mkt, "list_available_numbers", new=AsyncMock(return_value=AVAILABLE)
        ),
        patch.object(
            tm.db_client, "charge_purchase_tx", new=AsyncMock(return_value="unmetered")
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
    kyc, ensure = _gates()
    with (
        kyc,
        ensure,
        patch.object(
            tm.db_client, "get_organization_by_id", new=AsyncMock(return_value=_org_row())
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
