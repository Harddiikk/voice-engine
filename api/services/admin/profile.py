"""Per-client admin profile: plan override, custom pricing, suspend, notes,
plus money-in-INR derivation. Backed by one ``ADMIN_PROFILE`` org config record.

Pricing/plan fall back to global defaults when the client has no override, so
existing clients behave exactly as before until an admin customizes them.
"""

from __future__ import annotations

from datetime import UTC, datetime
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


async def is_org_suspended(organization_id: Optional[int]) -> bool:
    if organization_id is None:
        return False
    return bool((await get_admin_profile(organization_id)).get("suspended"))


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
