"""Telephony marketplace routes — buy a phone number after KYC, charged to credits.

GET /numbers (available pool) · GET /my-numbers (org's assigned, with local
fallback) · POST /buy (provision the org's VoiceLink client -> fail-closed KYC
gate -> validate the pool -> charge credits -> map the DID -> record locally).
"""

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services import telephony_marketplace as mkt
from api.services.admin.profile import get_org_pricing
from api.services.admin.suspend_gate import assert_org_not_suspended
from api.services.auth.depends import get_user
from api.services.voicelink_clients.client import VoiceLinkClientError
from api.services.voicelink_clients.service import ensure_voicelink_client
from api.services.voicelink_kyc.gating import assert_org_kyc_complete_for_purchase

router = APIRouter(prefix="/telephony/marketplace", tags=["telephony"])


class BuyNumberRequest(BaseModel):
    did_id: int


def _org(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="no_organization_selected")
    return user.selected_organization_id


async def _number_pricing(org: int) -> tuple[int, int]:
    """Per-client number price (INR) and the credit-seconds it costs at the org's
    per-minute rate. Falls back to the global defaults via ``get_org_pricing``.
    A non-positive rate (misconfigured override) yields 0 seconds rather than
    crashing the buy path.
    """
    pricing = await get_org_pricing(org)
    number_price_inr = pricing["number_price_inr"]
    per_minute = pricing["per_minute_inr"]
    number_setup_seconds = (
        int(round(number_price_inr / per_minute * 60)) if per_minute > 0 else 0
    )
    return number_price_inr, number_setup_seconds


@router.get("/numbers")
async def available_numbers(user: UserModel = Depends(get_user)):
    org = _org(user)
    number_price_inr, number_setup_seconds = await _number_pricing(org)
    return {
        "numbers": await mkt.list_available_numbers(),
        "price_inr": number_price_inr,
        "setup_seconds": number_setup_seconds,
    }


@router.get("/my-numbers")
async def my_numbers(user: UserModel = Depends(get_user)):
    org = _org(user)
    return {"numbers": await mkt.list_org_numbers_resolved(org)}


@router.post("/buy")
async def buy_number(body: BuyNumberRequest, user: UserModel = Depends(get_user)):
    org = _org(user)

    # A suspended org must not buy (or even provision) — clear 403 up front.
    await assert_org_not_suspended(org)

    # Provision the org's VoiceLink client FIRST (idempotent, best-effort) so
    # the KYC gate below can scope to the org's own client.
    await ensure_voicelink_client(org)
    o = await db_client.get_organization_by_id(org)
    client_id = o.voicelink_client_id if o else None
    if not client_id:
        raise HTTPException(status_code=400, detail="telephony_account_not_provisioned")

    # Buying a number is the compliance moment: the gate FAILS CLOSED
    # (403 incomplete KYC, 502 when the status can't be verified).
    await assert_org_kyc_complete_for_purchase(org)

    # Never trust the client-supplied did_id: it MUST be in the reseller's
    # available pool (prevents grabbing an arbitrary / another org's DID).
    available = await mkt.list_available_numbers()
    did = next(
        (
            n
            for n in available
            if n.get("did_id") is not None and int(n["did_id"]) == body.did_id
        ),
        None,
    )
    if did is None:
        raise HTTPException(status_code=409, detail="number_unavailable")
    did_number = str(did.get("did_number") or body.did_id)

    # Charge FIRST, atomically + conditionally (race-safe) with its ledger row,
    # so concurrent buys can't double-spend. Unlimited (NULL) orgs and
    # zero-cost are never charged ('unmetered'). Price + seconds are per-client.
    number_price_inr, cost = await _number_pricing(org)
    charge = await db_client.charge_purchase_tx(
        org,
        cost,
        kind="number_purchase",
        description=f"Phone number {did_number} — ₹{number_price_inr}",
    )
    if charge is None:
        raise HTTPException(status_code=402, detail="insufficient_credits")
    charged = isinstance(charge, int)

    # Assign the DID; refund the charge if the external map fails.
    try:
        await mkt.assign_number(client_id, body.did_id)
    except VoiceLinkClientError as e:
        if charged:
            await db_client.refund_tx(
                org,
                cost,
                description=f"Refund: phone number {did_number} assignment failed",
            )
        raise HTTPException(status_code=502, detail=f"assign_failed: {e}")

    # Local bookkeeping AFTER the DID is mapped upstream: persist the DID on
    # the org's config/phone rows and arm the KYC dialing gate. The org owns
    # the number at this point, so a failure here must NOT refund or raise —
    # log loudly and let my-numbers' local fallback / a retry heal it.
    try:
        await mkt.record_number_purchase(
            org,
            client_id=str(client_id),
            did_number=did_number,
            username=getattr(o, "voicelink_username", None),
        )
    except Exception:
        logger.exception(
            f"Number purchase bookkeeping failed for org {org} (did {did_number}); "
            f"the DID IS mapped on VoiceLink — reconcile manually"
        )

    new_balance = await db_client.get_free_call_seconds_remaining(org)
    return {
        "ok": True,
        "did_id": body.did_id,
        "did_number": did_number,
        "balance_seconds": new_balance,
    }
