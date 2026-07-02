"""Telephony marketplace — browse the reseller's available numbers + assign one.

Thin wrappers over the VoiceLink reseller API (list available DIDs, assign a
DID to the org's VoiceLink client, list the org's assigned DIDs) plus the local
bookkeeping shared by the buy flow and the admin assign-did route:
``persist_org_did`` records a DID on the org's ``voicelink`` telephony
configuration + phone-number rows, and ``record_number_purchase`` additionally
arms the KYC dialing gate (``voicelink_did_purchased_at``). Credit/charging
orchestration lives in routes/telephony_marketplace.py.
"""

from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger
from sqlalchemy.exc import IntegrityError

from api.db import db_client
from api.services.voicelink_clients.client import (
    VoiceLinkClientError,
    get_voicelink_clients_client,
)
from api.services.voicelink_kyc import resolve_org_voicelink_client_id
from api.services.voicelink_kyc.client import DEFAULT_VOICELINK_API_BASE

VOICELINK_PROVIDER = "voicelink"


def _norm_did(d: dict) -> dict:
    return {
        "did_id": d.get("did_id") or d.get("id"),
        "did_number": d.get("did_number"),
        "type_label": d.get("type_label"),
        "country_code": d.get("country_code"),
        "user_status": d.get("user_status"),
        "user_status_label": d.get("user_status_label"),
    }


async def list_available_numbers() -> list[dict]:
    """Available (unassigned) DIDs in the reseller pool. Empty if none/unconfigured."""
    client = get_voicelink_clients_client()
    if not client.is_configured:
        return []
    try:
        dids = await client.available_dids()
    except VoiceLinkClientError as e:
        logger.warning(f"VoiceLink available-dids failed: {e}")
        return []
    # user_status 1 = Available.
    return [_norm_did(d) for d in dids if str(d.get("user_status", "1")) == "1"]


async def assign_number(client_id: str, did_id) -> None:
    """Map an available DID to the org's VoiceLink client. Raises VoiceLinkClientError."""
    client = get_voicelink_clients_client()
    await client.map_did(
        {
            "client_id": client_id,
            "did_id": did_id,
            "call_recording": 1,
            "user_status": 2,  # Assigned
            "client_auto_renew": 1,
        }
    )


async def list_org_numbers(client_id: Optional[str]) -> list[dict]:
    """DIDs currently assigned to the org's VoiceLink client."""
    client = get_voicelink_clients_client()
    if not client_id or not client.is_configured:
        return []
    try:
        clients = await client.list_clients()
    except VoiceLinkClientError:
        return []
    for c in clients:
        if str(c.get("id")) == str(client_id):
            return [_norm_did(d) for d in (c.get("dids") or [])]
    return []


async def list_org_numbers_resolved(organization_id: int) -> list[dict]:
    """The org's numbers — live from VoiceLink, healed with local truth.

    Resolves the org's VoiceLink ``client_id`` and asks the reseller; when
    that comes back empty (no client id, reseller outage, drifted link) it
    falls back to what we recorded locally at purchase/assign time — the
    org's ``voicelink`` configs' phone-number rows and ``did_number``
    credentials — so an owned number never silently disappears from the UI.
    Local entries carry ``{"did_number": ..., "source": "local"}``.
    """
    client_id, _ = await resolve_org_voicelink_client_id(organization_id)
    live = await list_org_numbers(client_id)
    if live:
        return live

    numbers: list[dict] = []
    seen: set[str] = set()

    def _add(raw) -> None:
        if not raw:
            return
        did = str(raw).lstrip("+")
        if not did or did in seen:
            return
        seen.add(did)
        numbers.append({"did_number": did, "source": "local"})

    configs = await db_client.list_telephony_configurations_by_provider(
        organization_id, VOICELINK_PROVIDER
    )
    for config in sorted(configs, key=lambda c: not c.is_default_outbound):
        for row in await db_client.list_phone_numbers_for_config(config.id):
            _add(row.address)
        _add((config.credentials or {}).get("did_number"))
    return numbers


async def persist_org_did(
    organization_id: int,
    did_number: str,
    *,
    client_id: Optional[str] = None,
    username: Optional[str] = None,
) -> Tuple[int, bool]:
    """Record a DID on the org's ``voicelink`` telephony configuration.

    Updates the default-outbound VoiceLink configuration when one exists
    (stamping ``did_number``/``api_base``/``client_id``, preserving the other
    credentials and forcing it default-outbound), else creates a
    default-outbound "VoiceLink" configuration (also stamping ``username``
    when given). Then records the DID as an org phone-number row —
    ``IntegrityError`` (already recorded) is swallowed so the whole call is
    idempotent.

    Returns ``(configuration_id, created)``. Raises ``LookupError`` if the
    configuration vanished between lookup and update (races only).
    """
    configs = await db_client.list_telephony_configurations_by_provider(
        organization_id, VOICELINK_PROVIDER
    )

    if configs:
        target = sorted(configs, key=lambda c: not c.is_default_outbound)[0]
        credentials = dict(target.credentials or {})
        credentials["did_number"] = did_number
        credentials.setdefault("api_base", DEFAULT_VOICELINK_API_BASE)
        if client_id:
            credentials["client_id"] = str(client_id)
        updated = await db_client.update_telephony_configuration(
            target.id, organization_id, credentials=credentials
        )
        if updated is None:
            raise LookupError("telephony_configuration_not_found")
        if not updated.is_default_outbound:
            await db_client.set_default_telephony_configuration(
                updated.id, organization_id
            )
        configuration_id, created = updated.id, False
    else:
        credentials = {
            "api_base": DEFAULT_VOICELINK_API_BASE,
            "did_number": did_number,
        }
        if username:
            credentials["username"] = username
        if client_id:
            credentials["client_id"] = str(client_id)
        row = await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="VoiceLink",
            provider=VOICELINK_PROVIDER,
            credentials=credentials,
            is_default_outbound=True,
        )
        configuration_id, created = row.id, True

    # VoiceLink registers DIDs as bare digits (e.g. "919484959244"); store the
    # E.164 form. create_phone_number normalizes it for inbound lookups; a
    # duplicate (org-unique address) means it's already recorded.
    address = f"+{did_number}" if did_number.isdigit() else did_number
    try:
        await db_client.create_phone_number(
            organization_id=organization_id,
            telephony_configuration_id=configuration_id,
            address=address,
        )
    except IntegrityError:
        logger.debug(
            f"Phone number {address} already recorded for org {organization_id}"
        )

    return configuration_id, created


async def record_number_purchase(
    organization_id: int,
    *,
    client_id: str,
    did_number: str,
    username: Optional[str] = None,
) -> None:
    """Local bookkeeping after a marketplace DID purchase.

    Persists the DID on the org's VoiceLink config + phone rows, then arms the
    KYC dialing gate (first purchase stamps ``voicelink_did_purchased_at``).
    """
    await persist_org_did(
        organization_id, did_number, client_id=client_id, username=username
    )
    await db_client.mark_organization_did_purchased(organization_id)
