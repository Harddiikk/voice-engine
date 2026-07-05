"""Request/response schemas for the superuser admin Clients endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from api.schemas.kyc import KycStatusResponse
from api.services.plans import ASSIGNABLE_PLANS


class AdminClientItem(BaseModel):
    organization_id: int
    organization_name: str
    owner_user_id: Optional[int] = None
    owner_email: Optional[str] = None
    owner_provider_id: Optional[str] = None
    created_at: Optional[datetime] = None
    voicelink_status: Optional[str] = None
    voicelink_client_id: Optional[str] = None
    voicelink_username: Optional[str] = None
    voicelink_error: Optional[str] = None
    has_voicelink_config: bool = False
    did_number: Optional[str] = None
    # Live reconciliation against VoiceLink (GET /v1/reseller/clients):
    # "active" (exists in VoiceLink) | "missing" | "unconfigured" (reseller
    # creds unset) | "unknown" (reseller lookup failed → stored status shown).
    live_state: str = "unknown"
    live_client_id: Optional[str] = None
    # Remaining call-seconds balance; None = unmetered (unlimited).
    credits_seconds_remaining: Optional[int] = None
    # SaaS billing view (effective plan + money-in-INR at the client's rate).
    effective_plan: str = "trial"
    per_minute_inr: float = 0.0
    # Money remaining in INR; None = unmetered (unlimited).
    money_left_inr: Optional[float] = None
    money_spent_inr: float = 0.0
    suspended: bool = False


class AdminClientsListResponse(BaseModel):
    clients: List[AdminClientItem]


class RetryProvisionRequest(BaseModel):
    """A NEW VoiceLink password; provisioning keeps an encrypted display copy."""

    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class RetryProvisionResponse(BaseModel):
    voicelink_status: str
    voicelink_client_id: Optional[str] = None
    voicelink_username: Optional[str] = None
    voicelink_error: Optional[str] = None


class CreateClientRequest(BaseModel):
    """Optional password override for one-click create.

    Normally omitted — the endpoint reuses the org's stored (encrypted)
    signup password. A password is only supplied for legacy orgs that have no
    stored secret.
    """

    password: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class CreateClientResponse(BaseModel):
    action: str  # "linked" (already existed) | "created"
    voicelink_status: str
    voicelink_client_id: Optional[str] = None
    voicelink_username: Optional[str] = None
    voicelink_error: Optional[str] = None


class AssignDidRequest(BaseModel):
    did_number: str = Field(
        ...,
        min_length=1,
        description="DID in its VoiceLink-registered form (e.g. 919484959244)",
    )
    client_id: Optional[str] = Field(
        default=None,
        description=(
            "VoiceLink client id to stamp on the configuration; defaults to "
            "the org's provisioned voicelink_client_id"
        ),
    )
    arm_kyc: bool = Field(
        default=False,
        description=(
            "Also stamp voicelink_did_purchased_at so the KYC dialing gate "
            "applies to this org (marketplace purchases always do this)"
        ),
    )


class AssignDidResponse(BaseModel):
    configuration_id: int
    created: bool
    did_number: str
    client_id: Optional[str] = None


class GrantCreditsRequest(BaseModel):
    """Top-up for a metered org's call-credits balance (1 credit = 1 minute)."""

    minutes: int = Field(
        ...,
        ge=1,
        le=100_000,
        description="Minutes of call credit to grant (converted to seconds).",
    )


class GrantCreditsResponse(BaseModel):
    organization_id: int
    granted_seconds: int
    # Balance after the grant; never None here (unmetered orgs are rejected).
    credits_seconds_remaining: Optional[int] = None


class SetCreditsRequest(BaseModel):
    """Set a metered org's call-credits balance to an exact value — by minutes
    OR rupees.

    Unlike a grant (which adds), this pins the balance — used to correct it up
    or down (e.g. 9000 -> 6000 minutes). Provide **exactly one** of ``minutes``
    or ``rupees``; rupees is converted to credit-seconds server-side at the
    org's effective per-minute rate. ``0`` zeroes the balance.
    """

    minutes: Optional[int] = Field(
        default=None,
        ge=0,
        le=100_000,
        description="Exact minutes of call credit the org should have.",
    )
    rupees: Optional[float] = Field(
        default=None,
        ge=0,
        le=10_000_000,
        description="Exact ₹ balance the org should have (converted at its per-minute rate).",
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "SetCreditsRequest":
        if (self.minutes is None) == (self.rupees is None):
            raise ValueError("Provide exactly one of minutes or rupees")
        return self


class SetCreditsResponse(BaseModel):
    organization_id: int
    # Balance after the update (never None here — unmetered orgs are rejected).
    credits_seconds_remaining: Optional[int] = None


class ClientPasswordResponse(BaseModel):
    """The stored display copy of the client's VoiceLink portal password."""

    username: Optional[str] = None
    password: str


class RecordPasswordRequest(BaseModel):
    """A portal password to record (encrypted at rest, display-only).

    This is our copy of the password the owner set in the VoiceLink portal —
    recording it does NOT change the password on VoiceLink (there is no
    upstream change-password API).
    """

    password: str = Field(..., min_length=8)


class RecordPasswordResponse(BaseModel):
    organization_id: int
    stored: bool
    # Reminder surfaced to the operator: this is a record of the portal
    # password, not a password change on VoiceLink.
    note: str


class AdminKycStatusResponse(KycStatusResponse):
    """Per-org KYC status for the admin Clients view.

    Same shape as the self-serve ``GET /kyc/status`` response plus a
    ``status`` discriminator:

    - ``ok`` — the org's VoiceLink ``client_id`` resolved and the KYC status
      was fetched from VoiceLink.
    - ``no_client`` — the org has no resolvable VoiceLink client id (KYC
      would act on the reseller's own account, so we don't fetch).
    - ``disabled`` — reseller credentials are not configured.
    """

    status: str = "ok"
    client_id: Optional[str] = None


# ======== Per-client profile / detail (SaaS admin) ========


class AdminPricingCustom(BaseModel):
    """Which pricing fields are per-client overrides (for a "custom" badge)."""

    per_minute_inr: bool = False
    number_price_inr: bool = False
    setup_fee_inr: bool = False


class AdminPricing(BaseModel):
    """Effective per-client pricing (INR), global defaults where not overridden."""

    per_minute_inr: float
    number_price_inr: int
    setup_fee_inr: int
    custom: AdminPricingCustom


class AdminMoney(BaseModel):
    """Money view: balance + spend in both seconds and INR at the client's rate."""

    balance_seconds: Optional[int] = None
    unlimited: bool = False
    per_minute_inr: float = 0.0
    money_left_inr: Optional[float] = None
    spent_seconds: int = 0
    money_spent_inr: float = 0.0


class AdminNote(BaseModel):
    """One entry in the org's admin ops log."""

    at: Optional[str] = None
    by: Optional[int] = None
    text: str = ""


class AdminClientUsage(BaseModel):
    """Rolling usage summary (from get_organization_overview)."""

    period: str = "month"
    total_calls: int = 0
    total_minutes: float = 0.0
    connected_calls: int = 0
    money_spent_inr: float = 0.0


class AdminPlanCard(BaseModel):
    """Admin-designed plan card shown to the client on their Credits page.

    When enabled, the client sees ONLY this card (no packs/credit internals):
    title, price, features, expiry + a Purchase/Renew button through PayU.
    """

    title: str = Field(..., min_length=1, max_length=100)
    price_inr: float = Field(..., gt=0, le=10_000_000)
    included_minutes: int = Field(default=0, ge=0, le=1_000_000)
    features: List[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True

    @field_validator("features")
    @classmethod
    def strip_features(cls, v: List[str]) -> List[str]:
        return [f.strip() for f in v if f and f.strip()][:20]


class AdminClientDetailResponse(BaseModel):
    """Full per-client admin view (identity + plan + pricing + money + KYC)."""

    organization_id: int
    organization_name: str
    owner_user_id: Optional[int] = None
    owner_email: Optional[str] = None
    owner_provider_id: Optional[str] = None
    created_at: Optional[datetime] = None
    # VoiceLink state (stored — a lighter view than the list's live reconcile).
    voicelink_status: Optional[str] = None
    voicelink_client_id: Optional[str] = None
    voicelink_username: Optional[str] = None
    voicelink_error: Optional[str] = None
    has_voicelink_config: bool = False
    did_number: Optional[str] = None
    # Plan: ``plan`` is the effective tier (override wins); ``plan_override`` is
    # the raw admin override (null = derived from purchases).
    plan: str
    plan_override: Optional[str] = None
    features: Dict[str, bool] = Field(default_factory=dict)
    pricing: AdminPricing
    money: AdminMoney
    suspended: bool = False
    # When True the client also sees the Dograh managed voice + BYOK in the
    # model/voice editor; default False = Gemini voices only (all plans).
    show_dograh_voice: bool = False
    # True when a per-client Gemini key override is set (the key itself is never
    # returned); False = this client uses the shared platform Gemini key.
    has_gemini_key: bool = False
    # Client plan card + expiry (None = no card / never purchased).
    plan_card: Optional[AdminPlanCard] = None
    plan_expires_at: Optional[str] = None
    # What the client filled during first-run onboarding (ONBOARDING_PROFILE
    # org config): company, business_type, role, use_case, call volume, etc.
    # None when they haven't onboarded.
    onboarding_profile: Optional[Dict[str, Any]] = None
    notes: List[AdminNote] = Field(default_factory=list)
    kyc: AdminKycStatusResponse
    # Omitted (null) if the usage rollup could not be computed.
    usage: Optional[AdminClientUsage] = None


class AdminProfileUpdateRequest(BaseModel):
    """Partial per-client profile update. Only *sent* fields change; send a
    pricing/plan field as ``null`` to clear the override back to the default."""

    plan_override: Optional[str] = None
    per_minute_inr: Optional[float] = Field(default=None, ge=0)
    number_price_inr: Optional[int] = Field(default=None, ge=0)
    setup_fee_inr: Optional[int] = Field(default=None, ge=0)
    suspended: Optional[bool] = None
    # True = also show the Dograh managed voice + BYOK for this client;
    # False (default) = Gemini voices only.
    show_dograh_voice: Optional[bool] = None
    # Per-client Gemini API key override (overrides the shared platform key);
    # empty string clears it back to the platform key. Never returned.
    gemini_api_key: Optional[str] = None
    # Client plan card (send null to remove the card).
    plan_card: Optional[AdminPlanCard] = None
    # Plan expiry (ISO timestamp); null clears back to "never purchased".
    plan_expires_at: Optional[str] = None

    @field_validator("plan_override")
    @classmethod
    def validate_plan(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ASSIGNABLE_PLANS:
            raise ValueError(
                f"plan_override must be one of {ASSIGNABLE_PLANS} or null to clear"
            )
        return v


class AdminProfileResponse(BaseModel):
    """The client's profile after an update: effective plan + pricing + flags."""

    organization_id: int
    plan: str
    plan_override: Optional[str] = None
    features: Dict[str, bool] = Field(default_factory=dict)
    pricing: AdminPricing
    suspended: bool = False
    show_dograh_voice: bool = False
    has_gemini_key: bool = False


class AddNoteRequest(BaseModel):
    text: str = Field(..., min_length=1)


class AdminNotesResponse(BaseModel):
    organization_id: int
    notes: List[AdminNote] = Field(default_factory=list)


class ChargeSetupFeeRequest(BaseModel):
    """Optional override of the configured setup fee (INR)."""

    amount_inr: Optional[int] = Field(default=None, ge=1)


class ChargeSetupFeeResponse(BaseModel):
    organization_id: int
    fee_inr: int
    charged_seconds: int
    credits_seconds_remaining: Optional[int] = None
    money: AdminMoney


class CreateClientAccountRequest(BaseModel):
    """Create a brand-new client org + owner user."""

    email: str = Field(..., min_length=3)
    name: Optional[str] = None
    plan: Optional[str] = None
    initial_credit_minutes: Optional[int] = Field(default=None, ge=0, le=1_000_000)

    @field_validator("plan")
    @classmethod
    def validate_plan(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ASSIGNABLE_PLANS:
            raise ValueError(f"plan must be one of {ASSIGNABLE_PLANS}")
        return v


class CreateClientAccountResponse(BaseModel):
    organization_id: int
    owner_user_id: int
    owner_email: str
    organization_name: str
    plan: str
    credits_seconds_remaining: Optional[int] = None
    # Generated login password for the new owner (superuser-only response) — the
    # owner should change it after first login.
    temporary_password: str
    note: str


class AdminAuditItem(BaseModel):
    id: int
    actor_user_id: Optional[int] = None
    target_organization_id: Optional[int] = None
    action: str
    detail: Optional[dict] = None
    created_at: Optional[datetime] = None


class AdminAuditListResponse(BaseModel):
    items: List[AdminAuditItem] = Field(default_factory=list)
