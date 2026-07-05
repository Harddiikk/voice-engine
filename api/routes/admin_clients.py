"""Superuser admin "Clients" endpoints.

Lists every client organization (excluding the superuser's own orgs) with
its VoiceLink provisioning state, supports retrying a failed provisioning
with a freshly supplied password, reveals/records the org's stored
(Fernet-encrypted) copy of the client's VoiceLink portal password — a
display-only record for the owner to hand to clients; VoiceLink has no
change-password API, so real changes happen in their portal — and assigns
a DID by creating/updating the org's ``voicelink`` telephony configuration
row. All endpoints require superuser privileges.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from api.db import db_client
from api.db.credit_ledger_client import ALREADY_APPLIED, UNMETERED
from api.db.models import OrganizationModel, UserModel
from api.schemas.admin_clients import (
    AdminPlanCard,
    AddNoteRequest,
    AdminAuditItem,
    AdminAuditListResponse,
    AdminClientDetailResponse,
    AdminClientItem,
    AdminClientUsage,
    AdminClientsListResponse,
    AdminKycStatusResponse,
    AdminMoney,
    AdminNotesResponse,
    AdminPricing,
    AdminProfileResponse,
    AdminProfileUpdateRequest,
    AssignDidRequest,
    AssignDidResponse,
    ChargeSetupFeeRequest,
    ChargeSetupFeeResponse,
    ClientPasswordResponse,
    CreateClientAccountRequest,
    CreateClientAccountResponse,
    CreateClientRequest,
    CreateClientResponse,
    GrantCreditsRequest,
    GrantCreditsResponse,
    RecordPasswordRequest,
    RecordPasswordResponse,
    RetryProvisionRequest,
    RetryProvisionResponse,
    SetCreditsRequest,
    SetCreditsResponse,
)
from api.services.admin.audit import record_admin_action
from api.services.admin.profile import (
    append_note,
    get_admin_profile,
    get_org_money,
    get_org_pricing,
    is_org_suspended,
    setup_fee_seconds,
    update_admin_profile,
)
from api.services.auth.depends import (
    create_user_configuration_with_mps_key,
    get_superuser,
)
from api.services.plans import (
    ASSIGNABLE_PLANS,
    features_for_plan,
    get_org_plan,
)
from api.services.telephony_marketplace import persist_org_did
from api.services.voicelink_clients import (
    VoiceLinkClientError,
    derive_username,
    generate_client_password,
    get_voicelink_clients_client,
    provision_voicelink_client,
    stash_voicelink_signup_secret,
)
from api.services.voicelink_clients.secrets import (
    decrypt_provision_secret,
    encrypt_provision_secret,
)
from api.services.voicelink_kyc import (
    VoiceLinkKycError,
    get_kyc_client,
    resolve_org_voicelink_client_id,
)
from api.utils.auth import hash_password

router = APIRouter(prefix="/admin/clients", tags=["admin-clients"])

# Audit list lives at /admin/audit (not under /admin/clients) — a second router
# with the /admin prefix, mounted alongside the clients router.
audit_router = APIRouter(prefix="/admin", tags=["admin-audit"])

VOICELINK_PROVIDER = "voicelink"
VOICELINK_STATUS_PROVISIONED = "provisioned"


def _resolve_owner(organization: OrganizationModel) -> Optional[UserModel]:
    """The org owner: local signup creates orgs as ``org_<user.provider_id>``;
    fall back to the earliest member."""
    users: List[UserModel] = list(organization.users or [])
    if not users:
        return None
    for user in users:
        if f"org_{user.provider_id}" == organization.provider_id:
            return user
    return min(users, key=lambda u: u.id)


def _ordered_voicelink_configs(configs):
    """Default-outbound config first."""
    return sorted(configs, key=lambda c: not c.is_default_outbound)


def _build_live_index(records):
    """Index reseller client records by id and by lowercased username."""
    by_id = {}
    by_username = {}
    for record in records:
        record_id = record.get("id")
        if record_id is not None:
            by_id[str(record_id)] = record
        username = record.get("username")
        if username:
            by_username[str(username).lower()] = record
    return by_id, by_username


def _match_live_client_id(organization, owner, by_id, by_username):
    """The VoiceLink client id for this org if it exists live, else ``None``.

    Match precedence: stored ``client_id`` → stored ``username`` → the username
    we would derive for this org. Email is never matched on — it repeats across
    clients.
    """
    stored_id = organization.voicelink_client_id
    if stored_id and str(stored_id) in by_id:
        return str(stored_id)
    stored_username = organization.voicelink_username
    if stored_username and stored_username.lower() in by_username:
        return str(by_username[stored_username.lower()].get("id"))
    if owner and owner.email:
        derived = derive_username(owner.email, organization.id).lower()
        if derived in by_username:
            return str(by_username[derived].get("id"))
    return None


async def _load_live_index(vl_client):
    """Fetch the reseller client list once and index it.

    Returns ``(index, default_state)`` — ``index`` is ``None`` when no live
    lookup ran (reseller unconfigured, or the call failed), in which case every
    org takes ``default_state`` ("unconfigured" or "unknown").
    """
    if not vl_client.is_configured:
        return None, "unconfigured"
    try:
        records = await vl_client.list_clients()
        return _build_live_index(records), "active"
    except VoiceLinkClientError as e:
        logger.warning(f"VoiceLink live reconcile failed: {e}")
        return None, "unknown"


@router.get("", response_model=AdminClientsListResponse)
async def list_clients(
    user: UserModel = Depends(get_superuser),
) -> AdminClientsListResponse:
    """All client organizations (the superuser's own orgs are excluded).

    Reconciles each org against VoiceLink (one reseller call) so ``live_state``
    reflects whether the client actually exists there, and self-heals stored
    state when a client we lost the link to is rediscovered.
    """
    organizations = await db_client.list_organizations_with_users(
        exclude_user_id=user.id
    )

    vl_index, default_live_state = await _load_live_index(
        get_voicelink_clients_client()
    )

    clients: List[AdminClientItem] = []
    for organization in organizations:
        owner = _resolve_owner(organization)
        configs = await db_client.list_telephony_configurations_by_provider(
            organization.id, VOICELINK_PROVIDER
        )
        did_number = next(
            (
                (config.credentials or {}).get("did_number")
                for config in _ordered_voicelink_configs(configs)
                if (config.credentials or {}).get("did_number")
            ),
            None,
        )

        live_state = default_live_state
        live_client_id = None
        if vl_index is not None:
            by_id, by_username = vl_index
            live_client_id = _match_live_client_id(
                organization, owner, by_id, by_username
            )
            if live_client_id:
                live_state = "active"
                # Self-heal stored state when the link drifted (or was lost).
                if (
                    organization.voicelink_client_id != live_client_id
                    or organization.voicelink_status != VOICELINK_STATUS_PROVISIONED
                ):
                    # provision_secret is deliberately untouched — it is the
                    # retained display copy of the client's portal password.
                    await db_client.update_organization_voicelink(
                        organization.id,
                        client_id=live_client_id,
                        status=VOICELINK_STATUS_PROVISIONED,
                        error=None,
                    )
            else:
                live_state = "missing"

        # SaaS billing view — effective plan + money-in-INR. These are per-org
        # async reads (profile + balance + spend); acceptable for an admin list.
        plan = await get_org_plan(organization.id)
        money = await get_org_money(organization.id)
        suspended = await is_org_suspended(organization.id)

        clients.append(
            AdminClientItem(
                organization_id=organization.id,
                organization_name=organization.provider_id,
                owner_user_id=owner.id if owner else None,
                owner_email=owner.email if owner else None,
                owner_provider_id=owner.provider_id if owner else None,
                created_at=organization.created_at,
                voicelink_status=organization.voicelink_status,
                voicelink_client_id=organization.voicelink_client_id,
                voicelink_username=organization.voicelink_username,
                voicelink_error=organization.voicelink_error,
                has_voicelink_config=bool(configs),
                did_number=did_number,
                live_state=live_state,
                live_client_id=live_client_id,
                credits_seconds_remaining=organization.free_call_seconds_remaining,
                effective_plan=plan,
                per_minute_inr=money["per_minute_inr"],
                money_left_inr=money["money_left_inr"],
                money_spent_inr=money["money_spent_inr"],
                suspended=suspended,
            )
        )

    return AdminClientsListResponse(clients=clients)


@router.post("/{org_id}/retry-provision", response_model=RetryProvisionResponse)
async def retry_provision(
    org_id: int,
    request: RetryProvisionRequest,
    user: UserModel = Depends(get_superuser),
) -> RetryProvisionResponse:
    """Re-run VoiceLink client creation for an org.

    Uses the stored ``voicelink_username`` (or re-derives one) and the NEW
    password supplied in the body; provisioning keeps an encrypted copy of
    the password as the org's display record.
    """
    organization = await db_client.get_organization_with_users(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    client = get_voicelink_clients_client()
    if not client.is_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "VoiceLink reseller credentials are not configured — set "
                "VOICELINK_RESELLER_USERNAME and VOICELINK_RESELLER_PASSWORD"
            ),
        )

    owner = _resolve_owner(organization)
    if owner is None or not owner.email:
        raise HTTPException(
            status_code=400,
            detail="Organization has no member user with an email address",
        )

    result = await provision_voicelink_client(
        organization.id,
        email=owner.email,
        password=request.password,
        username=organization.voicelink_username or None,
        client=client,
    )
    logger.info(
        f"Superuser {user.id} retried VoiceLink provisioning for org {org_id}: "
        f"{result['status']}"
    )
    return RetryProvisionResponse(
        voicelink_status=result["status"],
        voicelink_client_id=result["client_id"],
        voicelink_username=result["username"],
        voicelink_error=result["error"],
    )


@router.post("/{org_id}/create", response_model=CreateClientResponse)
async def create_client(
    org_id: int,
    request: Optional[CreateClientRequest] = None,
    user: UserModel = Depends(get_superuser),
) -> CreateClientResponse:
    """One-click (re)provision of an org's VoiceLink client.

    Links the org if the client already exists in VoiceLink (no duplicate),
    otherwise creates it using the org's stored (encrypted) signup password so
    the VoiceLink client password matches the platform password. Legacy orgs
    with no stored secret get a 409 directing the operator to Retry with a
    password.
    """
    organization = await db_client.get_organization_with_users(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    vl_client = get_voicelink_clients_client()
    if not vl_client.is_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "VoiceLink reseller credentials are not configured — set "
                "VOICELINK_RESELLER_USERNAME and VOICELINK_RESELLER_PASSWORD"
            ),
        )

    owner = _resolve_owner(organization)
    if owner is None or not owner.email:
        raise HTTPException(
            status_code=400,
            detail="Organization has no member user with an email address",
        )

    # Reconcile first: if the client already exists, link it instead of
    # creating a duplicate (a duplicate username/email would 422 upstream).
    try:
        records = await vl_client.list_clients()
    except VoiceLinkClientError as e:
        logger.warning(f"VoiceLink reconcile before create failed: {e}")
        records = []
    by_id, by_username = _build_live_index(records)
    live_client_id = _match_live_client_id(organization, owner, by_id, by_username)
    if live_client_id:
        # provision_secret is deliberately untouched — it is the retained
        # display copy of the client's portal password.
        await db_client.update_organization_voicelink(
            org_id,
            client_id=live_client_id,
            status=VOICELINK_STATUS_PROVISIONED,
            error=None,
        )
        logger.info(
            f"Superuser {user.id} linked existing VoiceLink client "
            f"{live_client_id} to org {org_id}"
        )
        return CreateClientResponse(
            action="linked",
            voicelink_status=VOICELINK_STATUS_PROVISIONED,
            voicelink_client_id=live_client_id,
            voicelink_username=organization.voicelink_username,
            voicelink_error=None,
        )

    # Use the supplied override, else the stored (encrypted) password, else a
    # freshly generated one (the client never logs into VoiceLink directly, so a
    # generated password is fine — and provisioning now retains it for dialing).
    password = (
        (request.password if request and request.password else None)
        or decrypt_provision_secret(organization.voicelink_provision_secret)
        or generate_client_password()
    )

    result = await provision_voicelink_client(
        org_id,
        email=owner.email,
        password=password,
        username=organization.voicelink_username or None,
        client=vl_client,
    )
    logger.info(
        f"Superuser {user.id} created VoiceLink client for org {org_id}: "
        f"{result['status']}"
    )
    return CreateClientResponse(
        action="created",
        voicelink_status=result["status"],
        voicelink_client_id=result["client_id"],
        voicelink_username=result["username"],
        voicelink_error=result["error"],
    )


@router.post("/{org_id}/assign-did", response_model=AssignDidResponse)
async def assign_did(
    org_id: int,
    request: AssignDidRequest,
    user: UserModel = Depends(get_superuser),
) -> AssignDidResponse:
    """Create/update the org's ``voicelink`` telephony configuration with a DID.

    Thin wrapper over ``persist_org_did`` (shared with the marketplace buy
    flow): the config row is org-scoped and marked default for outbound, so
    the client can dial as soon as the owner maps the DID + channels in the
    VoiceLink portal. Manual assignment does NOT arm the KYC dialing gate
    unless ``arm_kyc`` is set (a marketplace purchase always arms it).
    """
    organization = await db_client.get_organization_by_id(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    client_id = request.client_id or organization.voicelink_client_id
    try:
        configuration_id, created = await persist_org_did(
            org_id,
            request.did_number,
            client_id=str(client_id) if client_id else None,
            username=organization.voicelink_username,
        )
    except LookupError:
        raise HTTPException(
            status_code=404, detail="Telephony configuration not found"
        )

    if request.arm_kyc:
        await db_client.mark_organization_did_purchased(org_id)

    logger.info(
        f"Superuser {user.id} assigned DID to org {org_id} "
        f"(configuration_id={configuration_id}, created={created}, "
        f"arm_kyc={request.arm_kyc})"
    )
    return AssignDidResponse(
        configuration_id=configuration_id,
        created=created,
        did_number=request.did_number,
        client_id=str(client_id) if client_id else None,
    )


@router.post("/{org_id}/grant-credits", response_model=GrantCreditsResponse)
async def grant_credits(
    org_id: int,
    request: GrantCreditsRequest,
    user: UserModel = Depends(get_superuser),
) -> GrantCreditsResponse:
    """Top up a metered org's call-credits balance (1 credit = 60 seconds).

    Unmetered orgs (NULL balance = unlimited) are rejected with 409: crediting
    them would silently convert unlimited to metered (``add_call_seconds``
    COALESCEs NULL to 0 before adding).
    """
    organization = await db_client.get_organization_by_id(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    if organization.free_call_seconds_remaining is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Organization is unmetered (unlimited credits); granting "
                "credits would convert it to a metered balance. Refusing."
            ),
        )

    granted_seconds = request.minutes * 60
    # Credit + ledger row in one transaction (kind=grant, attributed to the
    # superuser). Returns None only if the org turned unmetered concurrently.
    new_balance = await db_client.grant_credits_tx(
        org_id,
        granted_seconds,
        created_by=user.id,
        description=f"Admin grant: {request.minutes} minutes",
    )
    if new_balance is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Organization is unmetered (unlimited credits); granting "
                "credits would convert it to a metered balance. Refusing."
            ),
        )
    logger.info(
        f"Superuser {user.id} granted {request.minutes} minutes "
        f"({granted_seconds}s) to org {org_id}; balance now {new_balance}s"
    )
    return GrantCreditsResponse(
        organization_id=org_id,
        granted_seconds=granted_seconds,
        credits_seconds_remaining=new_balance,
    )


@router.post("/{org_id}/set-credits", response_model=SetCreditsResponse)
async def set_credits(
    org_id: int,
    request: SetCreditsRequest,
    user: UserModel = Depends(get_superuser),
) -> SetCreditsResponse:
    """Set a metered org's call-credits balance to an exact value (1 credit = 60s).

    Unlike ``grant-credits`` (which only adds), this pins the balance so an admin
    can correct it up or down (e.g. 9000 -> 6000 minutes, or 0 to zero it). The
    signed delta is recorded as an ``adjustment`` ledger row. Unmetered orgs
    (NULL balance = unlimited) are rejected with 409 — pinning a balance would
    silently convert unlimited to metered.
    """
    organization = await db_client.get_organization_by_id(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    if organization.free_call_seconds_remaining is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Organization is unmetered (unlimited credits); setting a "
                "balance would convert it to a metered balance. Refusing."
            ),
        )

    # The balance can be set by minutes or by rupees. Rupees is converted to
    # credit-seconds at the org's effective per-minute rate (server-side, so the
    # money math is authoritative and never drifts from the UI).
    if request.rupees is not None:
        pricing = await get_org_pricing(org_id)
        rate = pricing["per_minute_inr"]
        if rate <= 0:
            raise HTTPException(
                status_code=400,
                detail="Per-minute rate is zero; cannot set a balance by rupees.",
            )
        target_seconds = int(round(request.rupees / rate * 60))
        description = (
            f"Admin set balance: ₹{request.rupees:g} "
            f"(~{target_seconds // 60} min @ ₹{rate:g}/min)"
        )
        audit_detail = {
            "rupees": request.rupees,
            "per_minute_inr": rate,
            "seconds": target_seconds,
        }
    else:
        target_seconds = request.minutes * 60
        description = f"Admin set balance: {request.minutes} minutes"
        audit_detail = {"minutes": request.minutes, "seconds": target_seconds}

    new_balance = await db_client.set_credits_tx(
        org_id,
        target_seconds,
        created_by=user.id,
        description=description,
    )
    if new_balance is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Organization is unmetered (unlimited credits); setting a "
                "balance would convert it to a metered balance. Refusing."
            ),
        )

    await record_admin_action(
        actor_user_id=user.id,
        target_organization_id=org_id,
        action="set_credits",
        detail=audit_detail,
    )
    logger.info(
        f"Superuser {user.id} set org {org_id} balance to {target_seconds}s "
        f"({description}); balance now {new_balance}s"
    )
    return SetCreditsResponse(
        organization_id=org_id,
        credits_seconds_remaining=new_balance,
    )


@router.get("/{org_id}/password", response_model=ClientPasswordResponse)
async def reveal_client_password(
    org_id: int,
    user: UserModel = Depends(get_superuser),
) -> ClientPasswordResponse:
    """Reveal the stored copy of the client's VoiceLink portal password.

    This is the display-only record (dialing/KYC use reseller credentials);
    404 ``no_stored_password`` when nothing is stored, the encryption key is
    unset, or the stored token fails to decrypt.
    """
    organization = await db_client.get_organization_by_id(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    password = decrypt_provision_secret(organization.voicelink_provision_secret)
    if not password:
        raise HTTPException(status_code=404, detail="no_stored_password")

    logger.info(
        f"Superuser {user.id} revealed the stored VoiceLink password "
        f"for org {org_id}"
    )
    return ClientPasswordResponse(
        username=organization.voicelink_username,
        password=password,
    )


@router.post("/{org_id}/password", response_model=RecordPasswordResponse)
async def record_client_password(
    org_id: int,
    request: RecordPasswordRequest,
    user: UserModel = Depends(get_superuser),
) -> RecordPasswordResponse:
    """Record an encrypted display copy of the client's portal password.

    VoiceLink has NO change-password API — real changes happen in their
    portal. This only updates our stored record so the owner can hand the
    password to the client later; it does NOT change it on VoiceLink.
    """
    organization = await db_client.get_organization_by_id(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    encrypted = encrypt_provision_secret(request.password)
    if encrypted is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "VOICELINK_PROVISION_KEY is not configured — the password "
                "cannot be stored"
            ),
        )

    await db_client.update_organization_voicelink(
        org_id, provision_secret=encrypted
    )
    logger.info(
        f"Superuser {user.id} recorded a VoiceLink portal password "
        f"for org {org_id}"
    )
    return RecordPasswordResponse(
        organization_id=org_id,
        stored=True,
        note=(
            "Stored as a record of the portal password — this does not "
            "change the password on VoiceLink."
        ),
    )


async def _resolve_kyc_status(org_id: int) -> AdminKycStatusResponse:
    """Resolve a single org's VoiceLink KYC status (one upstream call).

    Shared by the on-demand KYC endpoint and the per-client detail view.
    Assumes the org exists (callers 404 first). Raises HTTPException(502) when
    the VoiceLink call fails.
    """
    kyc_client = get_kyc_client()
    if not kyc_client.is_configured:
        return AdminKycStatusResponse(status="disabled", enabled=False)

    client_id, has_voicelink_config = await resolve_org_voicelink_client_id(org_id)
    if not client_id:
        return AdminKycStatusResponse(
            status="no_client",
            enabled=True,
            has_voicelink_config=has_voicelink_config,
        )

    try:
        envelope = await kyc_client.get_status(client_id)
    except VoiceLinkKycError as e:
        raise HTTPException(status_code=502, detail=str(e))

    data = envelope.get("data") or {}
    return AdminKycStatusResponse(
        status="ok",
        enabled=True,
        client_id_configured=True,
        has_voicelink_config=has_voicelink_config,
        client_id=client_id,
        kyc_status=data.get("kyc_status_label") or data.get("kyc_status"),
        pan_verified=data.get("pan_verified"),
        aadhaar_verified=data.get("aadhaar_verified"),
        gst_verified=data.get("gst_verified"),
        is_complete=data.get("is_complete"),
        current_step=data.get("current_step"),
        account_type=data.get("account_type"),
    )


@router.get("/{org_id}/kyc-status", response_model=AdminKycStatusResponse)
async def get_client_kyc_status(
    org_id: int,
    user: UserModel = Depends(get_superuser),
) -> AdminKycStatusResponse:
    """On-demand KYC status for a single client org (one VoiceLink call).

    Resolves the org's VoiceLink ``client_id`` exactly like the self-serve
    KYC routes do (``resolve_org_voicelink_client_id``). Fetched per row on
    demand rather than in the list endpoint to avoid N upstream calls.
    """
    organization = await db_client.get_organization_by_id(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    return await _resolve_kyc_status(org_id)


async def _org_did_number(org_id: int) -> tuple[Optional[str], bool]:
    """The org's default-outbound VoiceLink DID + whether it has any config."""
    configs = await db_client.list_telephony_configurations_by_provider(
        org_id, VOICELINK_PROVIDER
    )
    did_number = next(
        (
            (config.credentials or {}).get("did_number")
            for config in _ordered_voicelink_configs(configs)
            if (config.credentials or {}).get("did_number")
        ),
        None,
    )
    return did_number, bool(configs)


async def _client_usage(org_id: int, money: dict) -> Optional[AdminClientUsage]:
    """Best-effort rolling usage summary. None if the rollup can't be computed."""
    try:
        overview = await db_client.get_organization_overview(org_id, "month")
        totals = overview.get("totals") or {}
        return AdminClientUsage(
            period=overview.get("period", "month"),
            total_calls=int(totals.get("total_calls") or 0),
            total_minutes=float(totals.get("total_minutes") or 0.0),
            connected_calls=int(totals.get("connected_calls") or 0),
            money_spent_inr=money["money_spent_inr"],
        )
    except Exception as exc:  # pragma: no cover - usage is a nice-to-have
        logger.warning(f"Usage rollup failed for org {org_id}: {exc}")
        return None


@router.get("/{org_id}", response_model=AdminClientDetailResponse)
async def get_client_detail(
    org_id: int,
    user: UserModel = Depends(get_superuser),
) -> AdminClientDetailResponse:
    """Full per-client admin view: identity, VoiceLink state, plan + features,
    pricing, money, suspend flag, ops notes, KYC, and a usage rollup.

    Uses the stored VoiceLink fields (a lighter view than the list's live
    reconcile). KYC degrades to ``status="error"`` if VoiceLink is unreachable
    so the rest of the detail still renders.
    """
    organization = await db_client.get_organization_with_users(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    owner = _resolve_owner(organization)
    did_number, has_config = await _org_did_number(org_id)

    profile = await get_admin_profile(org_id)
    plan = await get_org_plan(org_id)
    pricing = await get_org_pricing(org_id)
    money = await get_org_money(org_id)

    try:
        kyc = await _resolve_kyc_status(org_id)
    except HTTPException:
        kyc = AdminKycStatusResponse(status="error", enabled=True)

    usage = await _client_usage(org_id, money)

    return AdminClientDetailResponse(
        organization_id=organization.id,
        organization_name=organization.provider_id,
        owner_user_id=owner.id if owner else None,
        owner_email=owner.email if owner else None,
        owner_provider_id=owner.provider_id if owner else None,
        created_at=organization.created_at,
        voicelink_status=organization.voicelink_status,
        voicelink_client_id=organization.voicelink_client_id,
        voicelink_username=organization.voicelink_username,
        voicelink_error=organization.voicelink_error,
        has_voicelink_config=has_config,
        did_number=did_number,
        plan=plan,
        plan_override=profile.get("plan_override"),
        features=features_for_plan(plan),
        pricing=AdminPricing(**pricing),
        money=AdminMoney(**money),
        suspended=bool(profile.get("suspended")),
        show_dograh_voice=bool(profile.get("show_dograh_voice")),
        has_gemini_key=bool(profile.get("gemini_api_key")),
        plan_card=_plan_card_or_none(profile),
        plan_expires_at=profile.get("plan_expires_at"),
        notes=list(profile.get("notes") or []),
        kyc=kyc,
        usage=usage,
    )


def _plan_card_or_none(profile: dict):
    """Parse the stored plan-card dict; tolerate legacy/invalid shapes as None."""
    raw = profile.get("plan_card")
    if not isinstance(raw, dict):
        return None
    try:
        return AdminPlanCard(**raw)
    except Exception:
        return None


@router.patch("/{org_id}/profile", response_model=AdminProfileResponse)
async def update_client_profile(
    org_id: int,
    request: AdminProfileUpdateRequest,
    user: UserModel = Depends(get_superuser),
) -> AdminProfileResponse:
    """Partial per-client profile update — plan override, custom pricing,
    suspend. Only the fields present in the request body are changed; send a
    pricing/plan field as ``null`` to clear the override back to the default.
    """
    organization = await db_client.get_organization_by_id(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    sent = request.model_fields_set
    changes = {field: getattr(request, field) for field in sent}
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")

    # The plan card arrives as a Pydantic model; the profile stores plain JSON.
    if changes.get("plan_card") is not None:
        changes["plan_card"] = changes["plan_card"].model_dump()

    # Forward only the fields the caller actually sent, so unset fields keep
    # their current value (the sentinel default in update_admin_profile).
    await update_admin_profile(org_id, **changes)

    # Never write the raw Gemini key into the audit log — record presence only.
    audit_changes = dict(changes)
    if "gemini_api_key" in audit_changes:
        audit_changes["gemini_api_key"] = (
            "<set>" if (audit_changes["gemini_api_key"] or "").strip() else "<cleared>"
        )
    await record_admin_action(
        actor_user_id=user.id,
        target_organization_id=org_id,
        action="update_profile",
        detail=audit_changes,
    )
    logger.info(
        f"Superuser {user.id} updated profile for org {org_id}: {audit_changes}"
    )

    profile = await get_admin_profile(org_id)
    plan = await get_org_plan(org_id)
    pricing = await get_org_pricing(org_id)
    return AdminProfileResponse(
        organization_id=org_id,
        plan=plan,
        plan_override=profile.get("plan_override"),
        features=features_for_plan(plan),
        pricing=AdminPricing(**pricing),
        suspended=bool(profile.get("suspended")),
        show_dograh_voice=bool(profile.get("show_dograh_voice")),
        has_gemini_key=bool(profile.get("gemini_api_key")),
        plan_card=_plan_card_or_none(profile),
        plan_expires_at=profile.get("plan_expires_at"),
    )


@router.post("/{org_id}/notes", response_model=AdminNotesResponse)
async def add_client_note(
    org_id: int,
    request: AddNoteRequest,
    user: UserModel = Depends(get_superuser),
) -> AdminNotesResponse:
    """Append a timestamped note to the client's admin ops log."""
    organization = await db_client.get_organization_by_id(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    profile = await append_note(org_id, by_user_id=user.id, text=request.text)
    await record_admin_action(
        actor_user_id=user.id,
        target_organization_id=org_id,
        action="add_note",
        detail={"text": request.text.strip()},
    )
    return AdminNotesResponse(
        organization_id=org_id, notes=list(profile.get("notes") or [])
    )


@router.post("/{org_id}/charge-setup-fee", response_model=ChargeSetupFeeResponse)
async def charge_setup_fee(
    org_id: int,
    request: Optional[ChargeSetupFeeRequest] = None,
    user: UserModel = Depends(get_superuser),
) -> ChargeSetupFeeResponse:
    """Charge the client's one-time setup fee against their credit balance.

    Uses the client's configured ``setup_fee_inr`` (or an explicit
    ``amount_inr`` override), converted to credit-seconds at the client's
    per-minute rate. 400 when no fee is configured, 409 for unmetered orgs
    (nothing to charge against), 402 when the balance can't cover it.
    """
    organization = await db_client.get_organization_by_id(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    pricing = await get_org_pricing(org_id)
    rate = pricing["per_minute_inr"]
    fee_inr = (request.amount_inr if request and request.amount_inr else None) or int(
        pricing["setup_fee_inr"]
    )
    if fee_inr <= 0:
        raise HTTPException(
            status_code=400,
            detail="No setup fee configured for this client (and no amount_inr given)",
        )

    seconds = setup_fee_seconds(fee_inr, rate)
    if seconds <= 0:
        raise HTTPException(
            status_code=400, detail="Setup fee converts to zero credit-seconds"
        )

    result = await db_client.charge_purchase_tx(
        org_id, seconds, kind="setup_fee", description=f"Setup fee — ₹{fee_inr}"
    )
    if result == UNMETERED:
        raise HTTPException(
            status_code=409,
            detail="Organization is unmetered (unlimited credits) — nothing to charge",
        )
    if result == ALREADY_APPLIED:
        raise HTTPException(status_code=409, detail="Setup fee already charged")
    if result is None:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Insufficient balance to charge the setup fee "
                f"(needs {seconds}s of credit)"
            ),
        )

    await record_admin_action(
        actor_user_id=user.id,
        target_organization_id=org_id,
        action="charge_setup_fee",
        detail={"fee_inr": fee_inr, "charged_seconds": seconds, "new_balance": result},
    )
    logger.info(
        f"Superuser {user.id} charged setup fee ₹{fee_inr} ({seconds}s) to "
        f"org {org_id}; balance now {result}s"
    )
    money = await get_org_money(org_id)
    return ChargeSetupFeeResponse(
        organization_id=org_id,
        fee_inr=fee_inr,
        charged_seconds=seconds,
        credits_seconds_remaining=result,
        money=AdminMoney(**money),
    )


@router.post("", response_model=CreateClientAccountResponse)
async def create_client_account(
    request: CreateClientAccountRequest,
    user: UserModel = Depends(get_superuser),
) -> CreateClientAccountResponse:
    """Create a brand-new client: owner user + organization, then optionally set
    the plan and grant starter credits.

    A random login password is generated and returned once (superuser-only) so
    the operator can hand it to the client; the owner should change it after
    first login. The signup-time default model/MPS configuration is created
    best-effort (same as self-signup) so the client is ready to run.
    """
    email = request.email.strip().lower()
    existing = await db_client.get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Generated password (the client never receives the plaintext except via the
    # operator). Hashed for the platform login; stashed for VoiceLink provisioning.
    password = generate_client_password()
    owner = await db_client.create_user_with_email(
        email=email, password_hash=hash_password(password), name=request.name
    )

    org_provider_id = f"org_{owner.provider_id}"
    organization, _ = await db_client.get_or_create_organization_by_provider_id(
        org_provider_id=org_provider_id, user_id=owner.id
    )
    await db_client.add_user_to_organization(owner.id, organization.id)
    await db_client.update_user_selected_organization(owner.id, organization.id)

    # Best-effort default configuration (mirrors auth.signup — never fatal).
    try:
        mps_config = await create_user_configuration_with_mps_key(
            owner.id, organization.id, owner.provider_id
        )
        if mps_config:
            await db_client.update_user_configuration(owner.id, mps_config)
    except Exception:
        logger.warning(
            "Failed to create default configuration for admin-created client",
            exc_info=True,
        )

    # Best-effort: stash the generated password so the org's VoiceLink client can
    # be provisioned later with a matching password.
    await stash_voicelink_signup_secret(
        organization_id=organization.id, email=email, password=password
    )

    if request.plan:
        await update_admin_profile(organization.id, plan_override=request.plan)

    credits_remaining = organization.free_call_seconds_remaining
    if request.initial_credit_minutes:
        granted = await db_client.grant_credits_tx(
            organization.id,
            request.initial_credit_minutes * 60,
            created_by=user.id,
            description=f"Admin initial grant: {request.initial_credit_minutes} minutes",
        )
        if granted is not None:
            credits_remaining = granted

    plan = await get_org_plan(organization.id)
    await record_admin_action(
        actor_user_id=user.id,
        target_organization_id=organization.id,
        action="create_client",
        detail={
            "email": email,
            "plan": request.plan,
            "initial_credit_minutes": request.initial_credit_minutes,
        },
    )
    logger.info(
        f"Superuser {user.id} created client org {organization.id} "
        f"(owner {owner.id}, plan={plan})"
    )

    return CreateClientAccountResponse(
        organization_id=organization.id,
        owner_user_id=owner.id,
        owner_email=owner.email,
        organization_name=organization.provider_id,
        plan=plan,
        credits_seconds_remaining=credits_remaining,
        temporary_password=password,
        note=(
            "Owner login created with a generated password (returned once) — "
            "the owner should change it after first login."
        ),
    )


@audit_router.get("/audit", response_model=AdminAuditListResponse)
async def list_audit(
    org_id: Optional[int] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: UserModel = Depends(get_superuser),
) -> AdminAuditListResponse:
    """Admin audit log (newest first), optionally filtered to one client org."""
    rows = await db_client.list_admin_audit(
        target_organization_id=org_id, limit=limit, offset=offset
    )
    return AdminAuditListResponse(
        items=[
            AdminAuditItem(
                id=row.id,
                actor_user_id=row.actor_user_id,
                target_organization_id=row.target_organization_id,
                action=row.action,
                detail=row.detail,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )
