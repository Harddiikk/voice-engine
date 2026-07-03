"""Org plan tier + per-plan feature flags.

1 credit = 1 call-minute. An org's *plan tier* is the highest pack it has ever
paid for (Razorpay) — there is no plan column; the `payment_transactions` table
is the source of truth. Trial orgs (no successful purchase) sit below Starter
and get no paid features.

Feature gates (see CREDIT_PACKS[*]["features"] in api/constants.py):
  - api: REST API keys / Developers surface — Growth & Scale
  - mcp: MCP server — Scale only
"""

from typing import Iterable

from api.constants import CREDIT_PACKS

TRIAL_PLAN = "trial"

ENTERPRISE_PLAN = "enterprise"

# Higher rank = more capable. Trial is the floor for orgs that never purchased;
# enterprise is an admin-assigned top tier (not a purchasable pack).
PLAN_RANK = {TRIAL_PLAN: 0, "starter": 1, "growth": 2, "scale": 3, ENTERPRISE_PLAN: 4}

# Assignable plan tiers admin can set via the plan override (pack tiers +
# trial + enterprise).
ASSIGNABLE_PLANS = (TRIAL_PLAN, "starter", "growth", "scale", ENTERPRISE_PLAN)

_DEFAULT_FEATURES = {"api": False, "mcp": False}


def features_for_plan(plan: str) -> dict:
    """The feature flags for a plan tier. Trial / unknown tiers get nothing."""
    if plan == ENTERPRISE_PLAN:
        return {"api": True, "mcp": True}
    pack = next((p for p in CREDIT_PACKS if p["id"] == plan), None)
    feats = pack.get("features") if pack else None
    if isinstance(feats, dict):
        return {"api": bool(feats.get("api")), "mcp": bool(feats.get("mcp"))}
    return dict(_DEFAULT_FEATURES)


def plan_from_pack_ids(pack_ids: Iterable[str]) -> str:
    """Highest-ranked plan among the paid pack ids (default TRIAL_PLAN)."""
    best = TRIAL_PLAN
    for pid in pack_ids:
        if PLAN_RANK.get(pid, 0) > PLAN_RANK.get(best, 0):
            best = pid
    return best


async def get_org_plan(organization_id: int) -> str:
    """Resolve an org's plan tier.

    An admin-set ``plan_override`` (in the ADMIN_PROFILE config) wins; otherwise
    the tier derives from the org's successful purchases.
    """
    from api.db import db_client
    from api.services.admin.profile import get_admin_profile

    profile = await get_admin_profile(organization_id)
    override = profile.get("plan_override")
    if override in PLAN_RANK:
        return override

    pack_ids = await db_client.get_paid_pack_ids(organization_id)
    return plan_from_pack_ids(pack_ids)
