"""Per-client admin profile: plan override, custom pricing, suspend, notes,
plus money-in-INR derivation. Backed by one ``ADMIN_PROFILE`` org config record.

Pricing/plan fall back to global defaults when the client has no override, so
existing clients behave exactly as before until an admin customizes them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from api.constants import CAMPAIGN_SPEND_RATE_INR_PER_MINUTE, NUMBER_PRICE_INR
from api.db import db_client
from api.enums import OrganizationConfigurationKey

ADMIN_PROFILE_KEY = OrganizationConfigurationKey.ADMIN_PROFILE.value

# Bound the ops log so the JSON blob stays small (newest kept).
MAX_NOTES = 200

# Sentinel: "argument not passed" (vs None, which means "clear the override").
_UNSET = object()


async def get_admin_profile(organization_id: int) -> dict:
    """The org's admin profile dict (empty when never set)."""
    row = await db_client.get_configuration(organization_id, ADMIN_PROFILE_KEY)
    value = getattr(row, "value", None)
    return dict(value) if isinstance(value, dict) else {}


async def _save_admin_profile(organization_id: int, profile: dict) -> dict:
    await db_client.upsert_configuration(
        organization_id, ADMIN_PROFILE_KEY, profile
    )
    return profile


async def update_admin_profile(
    organization_id: int,
    *,
    plan_override: Any = _UNSET,
    per_minute_inr: Any = _UNSET,
    number_price_inr: Any = _UNSET,
    setup_fee_inr: Any = _UNSET,
    suspended: Any = _UNSET,
    show_dograh_voice: Any = _UNSET,
    gemini_api_key: Any = _UNSET,
    plan_card: Any = _UNSET,
    plan_expires_at: Any = _UNSET,
) -> dict:
    """Partial update — only the passed fields change. Pass ``None`` to clear a
    pricing/plan override back to the default; omit to leave unchanged."""
    profile = await get_admin_profile(organization_id)
    pricing = dict(profile.get("pricing") or {})

    if plan_override is not _UNSET:
        if plan_override:
            profile["plan_override"] = plan_override
        else:
            profile.pop("plan_override", None)
    if suspended is not _UNSET:
        profile["suspended"] = bool(suspended)
    if show_dograh_voice is not _UNSET:
        profile["show_dograh_voice"] = bool(show_dograh_voice)
    if gemini_api_key is not _UNSET:
        # Per-client Gemini key override (overrides the shared platform key).
        # Empty/None clears it back to the platform key.
        key = (gemini_api_key or "").strip() if gemini_api_key else ""
        if key:
            profile["gemini_api_key"] = key
        else:
            profile.pop("gemini_api_key", None)
    if plan_card is not _UNSET:
        # Admin-designed client plan card (dict) — None/empty clears it.
        if plan_card:
            profile["plan_card"] = dict(plan_card)
        else:
            profile.pop("plan_card", None)
    if plan_expires_at is not _UNSET:
        # ISO timestamp; None clears (plan becomes "never purchased").
        if plan_expires_at:
            profile["plan_expires_at"] = str(plan_expires_at)
        else:
            profile.pop("plan_expires_at", None)
    for key, val in (
        ("per_minute_inr", per_minute_inr),
        ("number_price_inr", number_price_inr),
        ("setup_fee_inr", setup_fee_inr),
    ):
        if val is not _UNSET:
            if val is None:
                pricing.pop(key, None)
            else:
                pricing[key] = val
    if pricing:
        profile["pricing"] = pricing
    else:
        profile.pop("pricing", None)

    return await _save_admin_profile(organization_id, profile)


async def append_note(organization_id: int, *, by_user_id: int, text: str) -> dict:
    """Append a timestamped note to the org's admin ops log (newest last)."""
    profile = await get_admin_profile(organization_id)
    notes = list(profile.get("notes") or [])
    notes.append(
        {
            "at": datetime.now(UTC).isoformat(),
            "by": by_user_id,
            "text": text.strip(),
        }
    )
    profile["notes"] = notes[-MAX_NOTES:]
    return await _save_admin_profile(organization_id, profile)


def pricing_from_profile(profile: dict) -> dict:
    """Effective pricing (INR) with global-default fallback."""
    p = profile.get("pricing") or {}
    pm = p.get("per_minute_inr")
    npr = p.get("number_price_inr")
    sf = p.get("setup_fee_inr")
    return {
        "per_minute_inr": float(pm)
        if pm is not None
        else float(CAMPAIGN_SPEND_RATE_INR_PER_MINUTE),
        "number_price_inr": int(npr) if npr is not None else int(NUMBER_PRICE_INR),
        "setup_fee_inr": int(sf) if sf is not None else 0,
        # Which fields are custom (for the admin UI to show "custom" badges).
        "custom": {
            "per_minute_inr": pm is not None,
            "number_price_inr": npr is not None,
            "setup_fee_inr": sf is not None,
        },
    }


async def get_org_pricing(organization_id: int) -> dict:
    """Effective per-client pricing (INR), falling back to global defaults."""
    return pricing_from_profile(await get_admin_profile(organization_id))


# One plan cycle. Renewals extend from the current expiry (or now if lapsed).
PLAN_PERIOD_DAYS = 30

# Client renewal banner starts this many days before expiry.
PLAN_WARN_DAYS = 5


def plan_state(profile: dict) -> dict:
    """The org's plan-card state: card config + expiry/warn/expired flags.

    ``expired`` is True only when a card is enabled AND an expiry exists AND it
    has passed — an enabled card with NO expiry means "never purchased yet"
    (warn in UI, but don't suspend; the credit wallet still gates calls).
    """
    card = profile.get("plan_card") or None
    enabled = bool(card and card.get("enabled", True))
    expires_raw = profile.get("plan_expires_at")
    expires_at: Optional[datetime] = None
    if isinstance(expires_raw, str) and expires_raw:
        try:
            expires_at = datetime.fromisoformat(expires_raw)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
        except ValueError:
            expires_at = None
    days_left = None
    expired = False
    if enabled and expires_at is not None:
        remaining = expires_at - datetime.now(UTC)
        days_left = max(0, remaining.days) if remaining.total_seconds() > 0 else 0
        expired = remaining.total_seconds() <= 0
    return {
        "enabled": enabled,
        "card": card if enabled else None,
        "expires_at": expires_at,
        "days_left": days_left,
        "expired": expired,
        "warn": bool(
            enabled
            and days_left is not None
            and not expired
            and days_left <= PLAN_WARN_DAYS
        ),
    }


async def get_org_plan_state(organization_id: int) -> dict:
    return plan_state(await get_admin_profile(organization_id))


def plan_reminder_already_sent(profile: dict, stage: str, expiry_iso: str) -> bool:
    """True when a reminder for this (expiry cycle, stage) was already sent — so
    the daily cron doesn't spam. A renewal changes the expiry, resetting this."""
    sent = profile.get("plan_reminders_sent") or {}
    return sent.get("expiry") == expiry_iso and stage in (sent.get("stages") or [])


async def record_plan_reminder(
    organization_id: int, stage: str, expiry_iso: str
) -> None:
    """Mark a reminder stage ('warn'/'expired') as sent for the current expiry."""
    profile = await get_admin_profile(organization_id)
    sent = profile.get("plan_reminders_sent") or {}
    if sent.get("expiry") != expiry_iso:
        sent = {"expiry": expiry_iso, "stages": []}
    if stage not in sent["stages"]:
        sent["stages"].append(stage)
    profile["plan_reminders_sent"] = sent
    await _save_admin_profile(organization_id, profile)


async def extend_plan_month(organization_id: int, *, txnid: str) -> datetime:
    """Activate/renew the org's plan for one cycle (idempotency handled by the
    caller via the payment CAS). Extends from the current expiry when renewing
    early, from now when lapsed/first purchase. Records the paying txnid."""
    profile = await get_admin_profile(organization_id)
    state = plan_state(profile)
    now = datetime.now(UTC)
    base = state["expires_at"] if state["expires_at"] and state["expires_at"] > now else now
    new_expiry = base + timedelta(days=PLAN_PERIOD_DAYS)
    profile["plan_expires_at"] = new_expiry.isoformat()
    profile["plan_last_txnid"] = txnid
    await _save_admin_profile(organization_id, profile)
    return new_expiry


async def is_org_suspended(organization_id: Optional[int]) -> bool:
    """Manual admin suspend OR an expired monthly plan (auto-lifts on renewal)."""
    if organization_id is None:
        return False
    profile = await get_admin_profile(organization_id)
    if profile.get("suspended"):
        return True
    return plan_state(profile)["expired"]


# "Today" for spend is a calendar day in the deployment's local zone (India),
# matching the default calling window. Org-specific timezones can be wired later.
_SPEND_DAY_TZ = ZoneInfo("Asia/Kolkata")


def _today_start_utc() -> datetime:
    """UTC instant of the start of the current local (IST) calendar day."""
    now_local = datetime.now(_SPEND_DAY_TZ)
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start_local.astimezone(UTC)


async def get_org_money(organization_id: int) -> dict:
    """Money view for a client: balance, total spent, and TODAY's spend — in
    both seconds and INR at the client's effective per-minute rate."""
    balance = await db_client.get_free_call_seconds_remaining(organization_id)
    pricing = await get_org_pricing(organization_id)
    rate = pricing["per_minute_inr"]
    spent_seconds = await db_client.sum_spent_seconds(organization_id)
    spent_today_seconds = await db_client.sum_spent_seconds(
        organization_id, since=_today_start_utc()
    )
    return {
        "balance_seconds": balance,
        "unlimited": balance is None,
        "per_minute_inr": rate,
        "money_left_inr": (
            round((balance or 0) / 60 * rate, 2) if balance is not None else None
        ),
        "spent_seconds": spent_seconds,
        "money_spent_inr": round(spent_seconds / 60 * rate, 2),
        "spent_today_seconds": spent_today_seconds,
        "money_spent_today_inr": round(spent_today_seconds / 60 * rate, 2),
    }


def setup_fee_seconds(setup_fee_inr: int, per_minute_inr: float) -> int:
    """Convert a one-time INR setup fee to credit-seconds at the client's rate."""
    if setup_fee_inr <= 0 or per_minute_inr <= 0:
        return 0
    return int(round(setup_fee_inr / per_minute_inr * 60))
