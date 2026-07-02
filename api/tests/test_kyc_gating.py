"""KYC gates: dialing gate (fail-open, only after a reseller-DID purchase) and
purchase gate (fail-closed — buying a number is the compliance moment)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.services.voicelink_kyc import VoiceLinkKycError
from api.services.voicelink_kyc import gating
from api.services.voicelink_kyc.gating import (
    KYC_INCOMPLETE_MESSAGE,
    assert_org_kyc_complete,
    assert_org_kyc_complete_for_purchase,
    is_org_kyc_complete,
)


def _org(purchased=True):
    return SimpleNamespace(
        voicelink_did_purchased_at=(
            datetime(2026, 7, 1, tzinfo=timezone.utc) if purchased else None
        )
    )


def _client(*, configured=True, status=None, error=None):
    c = AsyncMock()
    c.is_configured = configured
    if error is not None:
        c.get_status = AsyncMock(side_effect=error)
    else:
        c.get_status = AsyncMock(return_value=status)
    return c


def _patches(client, client_id, org=None):
    return (
        patch.object(gating, "get_kyc_client", return_value=client),
        patch.object(
            gating.db_client,
            "get_organization_by_id",
            new=AsyncMock(return_value=org if org is not None else _org()),
        ),
        patch.object(gating, "resolve_org_voicelink_client_id",
                     new=AsyncMock(return_value=(client_id, True))),
    )


async def _run_is_complete(client, client_id, org=None):
    p1, p2, p3 = _patches(client, client_id, org=org)
    with p1, p2, p3:
        return await is_org_kyc_complete(1)


# ======== dialing gate (fail-open) ========


async def test_kyc_not_configured_allows():
    assert await _run_is_complete(_client(configured=False), None) is True


async def test_no_voicelink_client_allows():
    assert await _run_is_complete(_client(status={"data": {"is_complete": False}}), None) is True


async def test_never_purchased_allows_even_with_incomplete_kyc():
    """Regression (org 4): a client_id + incomplete KYC must NOT block dialing
    when the org never bought a DID from our reseller pool."""
    client = _client(status={"data": {"is_complete": False}})
    assert await _run_is_complete(client, "cid", org=_org(purchased=False)) is True
    client.get_status.assert_not_awaited()  # gate short-circuits before VoiceLink


async def test_missing_org_allows():
    client = _client(status={"data": {"is_complete": False}})
    with (
        patch.object(gating, "get_kyc_client", return_value=client),
        patch.object(
            gating.db_client,
            "get_organization_by_id",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            gating,
            "resolve_org_voicelink_client_id",
            new=AsyncMock(return_value=("cid", True)),
        ),
    ):
        assert await is_org_kyc_complete(1) is True


async def test_complete_allows():
    assert await _run_is_complete(_client(status={"data": {"is_complete": True}}), "cid") is True


async def test_incomplete_blocks_after_purchase():
    assert await _run_is_complete(_client(status={"data": {"is_complete": False}}), "cid") is False


async def test_api_error_fails_open():
    assert await _run_is_complete(_client(error=VoiceLinkKycError("boom")), "cid") is True


async def test_assert_raises_403_when_incomplete():
    p1, p2, p3 = _patches(_client(status={"data": {"is_complete": False}}), "cid")
    with p1, p2, p3, pytest.raises(HTTPException) as exc:
        await assert_org_kyc_complete(1)
    assert exc.value.status_code == 403


async def test_assert_noop_when_complete():
    p1, p2, p3 = _patches(_client(status={"data": {"is_complete": True}}), "cid")
    with p1, p2, p3:
        await assert_org_kyc_complete(1)  # must not raise


# ======== purchase gate (fail-closed) ========


async def test_purchase_gate_allows_when_kyc_unconfigured():
    p1, p2, p3 = _patches(_client(configured=False), None)
    with p1, p2, p3:
        await assert_org_kyc_complete_for_purchase(1)  # must not raise


async def test_purchase_gate_400_without_client_id():
    p1, p2, p3 = _patches(_client(status={"data": {"is_complete": True}}), None)
    with p1, p2, p3, pytest.raises(HTTPException) as exc:
        await assert_org_kyc_complete_for_purchase(1)
    assert exc.value.status_code == 400
    assert exc.value.detail == "telephony_account_not_provisioned"


async def test_purchase_gate_403_when_incomplete():
    p1, p2, p3 = _patches(_client(status={"data": {"is_complete": False}}), "cid")
    with p1, p2, p3, pytest.raises(HTTPException) as exc:
        await assert_org_kyc_complete_for_purchase(1)
    assert exc.value.status_code == 403
    assert exc.value.detail == KYC_INCOMPLETE_MESSAGE


async def test_purchase_gate_502_fails_closed_on_api_error():
    p1, p2, p3 = _patches(_client(error=VoiceLinkKycError("boom")), "cid")
    with p1, p2, p3, pytest.raises(HTTPException) as exc:
        await assert_org_kyc_complete_for_purchase(1)
    assert exc.value.status_code == 502
    assert exc.value.detail == "kyc_status_unavailable"


async def test_purchase_gate_noop_when_complete():
    p1, p2, p3 = _patches(_client(status={"data": {"is_complete": True}}), "cid")
    with p1, p2, p3:
        await assert_org_kyc_complete_for_purchase(1)  # must not raise
