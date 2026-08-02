"""Platform-wide roll-up across client organizations.

The owner reads this screen to decide who to chase, so the numbers have to be
right in the awkward cases: an unmetered client must not be counted as
"running dry", one broken client must not blank the whole view, and a
zero-call platform must not divide by zero computing success rate.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.admin.platform_overview import (
    LOW_BALANCE_SECONDS,
    build_platform_overview,
)


def _org(org_id: int, name: str, remaining):
    return SimpleNamespace(
        id=org_id, provider_id=name, free_call_seconds_remaining=remaining
    )


def _overview(calls: int, minutes: float, connected: int):
    return {
        "totals": {
            "total_calls": calls,
            "total_minutes": minutes,
            "connected_calls": connected,
        }
    }


def _patches(orgs, overviews, money=None, suspended=None, tags=None):
    money = money or {}
    suspended = suspended or {}
    tags = tags or {}
    return (
        patch(
            "api.services.admin.platform_overview.db_client.list_organizations_with_users",
            new=AsyncMock(return_value=orgs),
        ),
        patch(
            "api.services.admin.platform_overview.db_client.get_organization_overview",
            new=AsyncMock(side_effect=lambda oid, period=None: overviews[oid]),
        ),
        patch(
            "api.services.admin.platform_overview.get_org_money",
            new=AsyncMock(
                side_effect=lambda oid: {"money_spent_inr": money.get(oid, 0.0)}
            ),
        ),
        patch(
            "api.services.admin.platform_overview.is_org_suspended",
            new=AsyncMock(side_effect=lambda oid: suspended.get(oid, False)),
        ),
        patch(
            "api.services.admin.platform_overview.get_org_tags",
            new=AsyncMock(side_effect=lambda oid: tags.get(oid, [])),
        ),
    )


@pytest.mark.asyncio
async def test_aggregates_calls_minutes_and_revenue():
    orgs = [_org(1, "alpha", 3600), _org(2, "beta", 7200)]
    overviews = {1: _overview(10, 20.0, 6), 2: _overview(5, 8.0, 4)}
    p = _patches(orgs, overviews, money={1: 100.0, 2: 50.0})
    with p[0], p[1], p[2], p[3], p[4]:
        result = await build_platform_overview()

    totals = result["totals"]
    assert totals["clients"] == 2
    assert totals["total_calls"] == 15
    assert totals["total_minutes"] == 28.0
    assert totals["connected_calls"] == 10
    assert totals["revenue_inr"] == 150.0
    assert totals["outstanding_credit_seconds"] == 10800


@pytest.mark.asyncio
async def test_success_rate_does_not_divide_by_zero():
    orgs = [_org(1, "alpha", 600)]
    p = _patches(orgs, {1: _overview(0, 0.0, 0)})
    with p[0], p[1], p[2], p[3], p[4]:
        result = await build_platform_overview()
    assert result["totals"]["success_rate"] == 0.0


@pytest.mark.asyncio
async def test_unmetered_client_is_not_counted_as_low_balance():
    # NULL balance = unlimited. Counting it as "running dry" would send the
    # owner chasing a client who has nothing to buy.
    orgs = [_org(1, "unlimited", None)]
    p = _patches(orgs, {1: _overview(3, 1.0, 2)})
    with p[0], p[1], p[2], p[3], p[4]:
        result = await build_platform_overview()

    totals = result["totals"]
    assert totals["unmetered_clients"] == 1
    assert totals["low_balance_clients"] == 0
    assert totals["outstanding_credit_seconds"] == 0


@pytest.mark.asyncio
async def test_low_balance_counted_at_and_below_the_floor():
    orgs = [
        _org(1, "at-floor", LOW_BALANCE_SECONDS),
        _org(2, "below", 60),
        _org(3, "healthy", LOW_BALANCE_SECONDS + 1),
    ]
    overviews = {i: _overview(1, 1.0, 1) for i in (1, 2, 3)}
    p = _patches(orgs, overviews)
    with p[0], p[1], p[2], p[3], p[4]:
        result = await build_platform_overview()
    assert result["totals"]["low_balance_clients"] == 2


@pytest.mark.asyncio
async def test_active_vs_idle_split():
    orgs = [_org(1, "calling", 600), _org(2, "silent", 600)]
    overviews = {1: _overview(4, 2.0, 3), 2: _overview(0, 0.0, 0)}
    p = _patches(orgs, overviews)
    with p[0], p[1], p[2], p[3], p[4]:
        result = await build_platform_overview()

    assert result["totals"]["active_clients"] == 1
    assert result["totals"]["idle_clients"] == 1


@pytest.mark.asyncio
async def test_one_broken_client_does_not_blank_the_platform():
    # A single org whose overview raises must be skipped, not fatal.
    orgs = [_org(1, "ok", 600), _org(2, "broken", 600)]

    def _side_effect(oid, period=None):
        if oid == 2:
            raise RuntimeError("overview exploded")
        return _overview(9, 4.0, 5)

    with (
        patch(
            "api.services.admin.platform_overview.db_client.list_organizations_with_users",
            new=AsyncMock(return_value=orgs),
        ),
        patch(
            "api.services.admin.platform_overview.db_client.get_organization_overview",
            new=AsyncMock(side_effect=_side_effect),
        ),
        patch(
            "api.services.admin.platform_overview.get_org_money",
            new=AsyncMock(return_value={"money_spent_inr": 0.0}),
        ),
        patch(
            "api.services.admin.platform_overview.is_org_suspended",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "api.services.admin.platform_overview.get_org_tags",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_platform_overview()

    # Both clients still listed; only the healthy one contributes calls.
    assert result["totals"]["clients"] == 2
    assert result["totals"]["total_calls"] == 9


@pytest.mark.asyncio
async def test_tag_counts_and_client_sort_order():
    orgs = [_org(1, "quiet", 600), _org(2, "busy", 600)]
    overviews = {1: _overview(2, 1.0, 1), 2: _overview(40, 20.0, 30)}
    p = _patches(orgs, overviews, tags={1: ["gym"], 2: ["gym", "via shreyas"]})
    with p[0], p[1], p[2], p[3], p[4]:
        result = await build_platform_overview()

    # Busiest first, so the owner scans real usage at a glance.
    assert [c["organization_name"] for c in result["clients"]] == ["busy", "quiet"]
    # Most-used tag first.
    assert result["tags"][0] == {"tag": "gym", "clients": 2}
