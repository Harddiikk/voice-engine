"""Gate outbound calling and number purchase on VoiceLink KYC completion.

Outbound (campaign start/resume, public API trigger) must not run until the
org's VoiceLink KYC is complete — but the gate only applies to orgs that have
bought a number from our reseller pool. Design choices:

- Gate at campaign START/RESUME + public trigger (NOT per-dial) — one status
  check, no per-call latency or external dependency on the hot dialing path.

Dialing gate (``assert_org_kyc_complete`` — FAIL-OPEN):
- KYC not configured in the deployment -> ALLOWED (this deployment doesn't use KYC).
- Org never bought a DID from our pool (``voicelink_did_purchased_at`` IS NULL)
  -> ALLOWED (it dials via its own/BYO telephony or the shared reseller account;
  the number purchase is the KYC choke point).
- Org has no VoiceLink client_id -> ALLOWED (it dials via the shared reseller
  account, whose KYC the reseller owns; or telephony isn't set up and fails
  downstream anyway).
- is_complete True -> ALLOWED; False -> BLOCKED (403).
- VoiceLink status API errors -> FAIL-OPEN with a warning, so a reseller outage
  can't halt all calling (VoiceLink also enforces KYC downstream).

Purchase gate (``assert_org_kyc_complete_for_purchase`` — FAIL-CLOSED): buying
a number is the compliance moment, so incomplete KYC -> 403 and an unavailable
status API -> 502 ``kyc_status_unavailable`` (never sell a number unverified).
"""

from __future__ import annotations

from fastapi import HTTPException
from loguru import logger

from api.db import db_client
from api.services.voicelink_kyc import (
    VoiceLinkKycError,
    get_kyc_client,
    resolve_org_voicelink_client_id,
)

KYC_INCOMPLETE_MESSAGE = (
    "Complete your KYC verification before starting outbound calls."
)


async def is_org_kyc_complete(organization_id: int) -> bool:
    """True if the org may dial outbound (see module docstring for the gate rules)."""
    client = get_kyc_client()
    if not client.is_configured:
        return True
    organization = await db_client.get_organization_by_id(organization_id)
    if organization is None or organization.voicelink_did_purchased_at is None:
        logger.debug(
            f"KYC gate: org {organization_id} never bought a reseller DID; allowing"
        )
        return True
    client_id, _ = await resolve_org_voicelink_client_id(organization_id)
    if not client_id:
        return True
    try:
        envelope = await client.get_status(client_id)
    except VoiceLinkKycError as exc:
        logger.warning(
            f"KYC status check failed for org {organization_id}; allowing (fail-open): {exc}"
        )
        return True
    data = (envelope or {}).get("data") or {}
    return bool(data.get("is_complete"))


async def assert_org_kyc_complete(organization_id: int) -> None:
    """Raise 403 if the org's KYC isn't complete; no-op otherwise."""
    if not await is_org_kyc_complete(organization_id):
        raise HTTPException(status_code=403, detail=KYC_INCOMPLETE_MESSAGE)


async def assert_org_kyc_complete_for_purchase(organization_id: int) -> None:
    """Purchase-time KYC gate — FAIL-CLOSED (buying a number is the compliance moment).

    Requires a VoiceLink ``client_id`` (the buy route provisions the client
    before calling this, so a missing id means provisioning failed -> 400).
    Incomplete KYC -> 403; VoiceLink status API failure -> 502
    ``kyc_status_unavailable`` (we never sell a number we can't verify).
    """
    client = get_kyc_client()
    if not client.is_configured:
        return
    client_id, _ = await resolve_org_voicelink_client_id(organization_id)
    if not client_id:
        raise HTTPException(
            status_code=400, detail="telephony_account_not_provisioned"
        )
    try:
        envelope = await client.get_status(client_id)
    except VoiceLinkKycError as exc:
        logger.warning(
            f"KYC status check failed for org {organization_id} at purchase; "
            f"blocking (fail-closed): {exc}"
        )
        raise HTTPException(status_code=502, detail="kyc_status_unavailable")
    data = (envelope or {}).get("data") or {}
    if not data.get("is_complete"):
        raise HTTPException(status_code=403, detail=KYC_INCOMPLETE_MESSAGE)
