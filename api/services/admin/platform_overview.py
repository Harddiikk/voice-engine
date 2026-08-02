"""Platform-wide roll-up across every client organization.

The existing analytics surface answers "how is THIS org doing" — it is scoped
to the caller's selected organization. The deployment owner needs the opposite
view: one screen covering every client at once (who is calling, who is idle,
who is nearly out of credits, what the whole platform earned). This module
aggregates that.

Aggregation is per-org in a loop rather than one wide SQL query, because the
per-org numbers already exist behind ``get_organization_overview`` /
``get_org_money`` and duplicating that logic in SQL would give two sources of
truth that drift. Client counts here are dozens, not millions.
"""

from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.services.admin.profile import (
    get_org_money,
    get_org_tags,
    is_org_suspended,
)

# An org counts as "active" when it has placed at least one call in the window.
# Anything else is idle — which for the owner is a churn signal, not a metric.
ACTIVE_MIN_CALLS = 1

# Balances at or under this many seconds are surfaced as "running dry" so the
# owner sees them before the client's own low-credit alert fires.
LOW_BALANCE_SECONDS = 30 * 60


async def _safe_overview(organization_id: int, period: str) -> Optional[dict]:
    """Per-org overview, or None when it can't be built.

    One client with a broken overview must not blank the whole platform view,
    so failures are logged and skipped rather than raised.
    """
    try:
        return await db_client.get_organization_overview(
            organization_id, period=period
        )
    except Exception as e:
        logger.warning(
            f"platform-overview: skipping org {organization_id} — "
            f"overview failed: {e}"
        )
        return None


def _totals_of(overview: Any) -> dict:
    """Pull the totals block out of an overview in dict or model form."""
    if overview is None:
        return {}
    totals = (
        overview.get("totals")
        if isinstance(overview, dict)
        else getattr(overview, "totals", None)
    )
    if totals is None:
        return {}
    if isinstance(totals, dict):
        return totals
    return {
        "total_calls": getattr(totals, "total_calls", 0),
        "total_minutes": getattr(totals, "total_minutes", 0.0),
        "connected_calls": getattr(totals, "connected_calls", 0),
    }


async def build_platform_overview(
    *, exclude_user_id: Optional[int] = None, period: str = "month"
) -> dict:
    """Aggregate every client org into one owner-facing summary.

    ``exclude_user_id`` drops the owner's own organizations so the platform
    numbers describe clients, not the owner's test tenant.
    """
    organizations = await db_client.list_organizations_with_users(
        exclude_user_id=exclude_user_id
    )

    total_calls = 0
    total_minutes = 0.0
    connected_calls = 0
    revenue_inr = 0.0
    outstanding_seconds = 0
    active_clients = 0
    suspended_clients = 0
    unmetered_clients = 0
    low_balance_clients = 0
    tag_counts: dict[str, int] = {}
    clients: list[dict] = []

    for organization in organizations:
        overview = await _safe_overview(organization.id, period)
        totals = _totals_of(overview)

        calls = int(totals.get("total_calls") or 0)
        minutes = float(totals.get("total_minutes") or 0.0)
        connected = int(totals.get("connected_calls") or 0)

        money = await get_org_money(organization.id)
        suspended = await is_org_suspended(organization.id)
        tags = await get_org_tags(organization.id)

        remaining = organization.free_call_seconds_remaining
        unmetered = remaining is None

        total_calls += calls
        total_minutes += minutes
        connected_calls += connected
        revenue_inr += float(money.get("money_spent_inr") or 0.0)

        if calls >= ACTIVE_MIN_CALLS:
            active_clients += 1
        if suspended:
            suspended_clients += 1
        if unmetered:
            unmetered_clients += 1
        else:
            outstanding_seconds += max(0, int(remaining or 0))
            if (remaining or 0) <= LOW_BALANCE_SECONDS:
                low_balance_clients += 1

        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        clients.append(
            {
                "organization_id": organization.id,
                "organization_name": organization.provider_id,
                "calls": calls,
                "minutes": round(minutes, 1),
                "connected_calls": connected,
                "money_spent_inr": round(
                    float(money.get("money_spent_inr") or 0.0), 2
                ),
                "credits_seconds_remaining": remaining,
                "unmetered": unmetered,
                "suspended": suspended,
                "tags": tags,
            }
        )

    # Busiest first — the owner scans for who is actually using the platform.
    clients.sort(key=lambda c: c["calls"], reverse=True)

    return {
        "period": period,
        "totals": {
            "clients": len(organizations),
            "active_clients": active_clients,
            "idle_clients": len(organizations) - active_clients,
            "suspended_clients": suspended_clients,
            "unmetered_clients": unmetered_clients,
            "low_balance_clients": low_balance_clients,
            "total_calls": total_calls,
            "total_minutes": round(total_minutes, 1),
            "connected_calls": connected_calls,
            "success_rate": (
                round(connected_calls / total_calls * 100, 1)
                if total_calls
                else 0.0
            ),
            "revenue_inr": round(revenue_inr, 2),
            "outstanding_credit_seconds": outstanding_seconds,
        },
        "tags": [
            {"tag": tag, "clients": count}
            for tag, count in sorted(
                tag_counts.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ],
        "clients": clients,
    }
