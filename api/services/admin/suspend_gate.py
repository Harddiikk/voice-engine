"""Suspend gate — block a suspended org from outbound dialing + number purchase.

Mirrors the KYC gate call sites (campaign start/resume, public trigger, marketplace
buy) but is a pure local check on the org's admin profile ``suspended`` flag, so a
suspended client gets a clear 403 BEFORE the KYC gate runs (and before we provision
or charge anything). Kept in its own module so the suspend concern doesn't get tangled
into the KYC gating logic.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from api.services.admin.profile import is_org_suspended

# Machine-readable code + the human message the UI surfaces to the client.
SUSPENDED_CODE = "account_suspended"
SUSPENDED_MESSAGE = "This account is suspended. Contact your administrator."


async def assert_org_not_suspended(organization_id: Optional[int]) -> None:
    """Raise 403 ``account_suspended`` if the org is suspended; no-op otherwise."""
    if await is_org_suspended(organization_id):
        raise HTTPException(
            status_code=403,
            detail={"code": SUSPENDED_CODE, "message": SUSPENDED_MESSAGE},
        )
