"""Per-client campaign spend rate: ``_build_campaign_response`` prices spend at the
campaign org's rate, and the list + detail endpoints resolve that rate via
``get_org_pricing`` (fetched ONCE for a whole list).

``api.routes.campaign`` can't be imported directly in the test venv — its
``campaign.runner`` import transitively pulls pipecat azure/google speech deps
that aren't installed. We stub that one hub module before import; everything else
under test (``_build_campaign_response``, ``get_campaign``, ``get_campaigns``,
``_get_org_spend_rate``) is the real code.
"""

import sys
import types
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

# Stub the azure/google-heavy runner hub so campaign.py imports in this venv.
if "api.services.campaign.runner" not in sys.modules:
    _runner_stub = types.ModuleType("api.services.campaign.runner")
    _runner_stub.campaign_runner_service = object()
    sys.modules["api.services.campaign.runner"] = _runner_stub

from api.routes import campaign as camp  # noqa: E402


def _pricing(per_minute):
    return {
        "per_minute_inr": per_minute,
        "number_price_inr": 500,
        "setup_fee_inr": 0,
        "custom": {},
    }


def _campaign(org_id=4, cid=1):
    return SimpleNamespace(
        id=cid,
        name="C1",
        workflow_id=10,
        organization_id=org_id,
        state="running",
        source_type="csv",
        source_id="k",
        total_rows=5,
        processed_rows=1,
        failed_rows=0,
        created_at=datetime(2026, 7, 1),
        started_at=None,
        completed_at=None,
        retry_config=None,
        orchestrator_metadata=None,
        telephony_configuration_id=None,
        logs=[],
    )


def _user(org=4):
    return SimpleNamespace(id=7, selected_organization_id=org)


# ======== the pure response builder ========


def test_build_campaign_response_uses_per_client_rate():
    resp = camp._build_campaign_response(
        _campaign(), "WF", total_call_seconds=120, spend_rate_inr_per_minute=12.0
    )
    assert resp.spent_seconds == 120
    assert resp.spent_minutes == 2.0
    assert resp.spent_inr == 24.0  # 2.0 min * ₹12


def test_build_campaign_response_defaults_to_global_rate():
    resp = camp._build_campaign_response(_campaign(), "WF", total_call_seconds=60)
    assert resp.spent_inr == round(
        1.0 * camp.CAMPAIGN_SPEND_RATE_INR_PER_MINUTE, 2
    )


async def test_get_org_spend_rate_reads_per_client_pricing():
    with patch.object(
        camp, "get_org_pricing", new=AsyncMock(return_value=_pricing(15.0))
    ):
        assert await camp._get_org_spend_rate(4) == 15.0


# ======== detail endpoint threads the per-client rate ========


async def test_get_campaign_detail_prices_spend_at_org_rate():
    c = _campaign()
    with (
        patch.object(camp.db_client, "get_campaign", new=AsyncMock(return_value=c)),
        patch.object(
            camp.db_client, "get_workflow_name", new=AsyncMock(return_value="WF")
        ),
        patch.object(
            camp.db_client,
            "get_queued_runs_stats_for_campaigns",
            new=AsyncMock(return_value={1: {"executed": 0, "total": 0}}),
        ),
        patch.object(
            camp.db_client,
            "get_campaign_total_call_seconds",
            new=AsyncMock(return_value=300),
        ),
        patch.object(
            camp, "get_org_pricing", new=AsyncMock(return_value=_pricing(12.0))
        ),
    ):
        resp = await camp.get_campaign(1, user=_user())
    assert resp.spent_minutes == 5.0  # 300s
    assert resp.spent_inr == 60.0  # 5.0 min * ₹12


# ======== list endpoint resolves the rate ONCE and prices all campaigns ========


async def test_get_campaigns_list_prices_all_at_one_org_rate():
    c1, c2 = _campaign(cid=1), _campaign(cid=2)
    pricing = AsyncMock(return_value=_pricing(10.0))
    with (
        patch.object(
            camp.db_client, "get_campaigns", new=AsyncMock(return_value=[c1, c2])
        ),
        patch.object(
            camp.db_client,
            "get_workflows_by_ids",
            new=AsyncMock(return_value=[SimpleNamespace(id=10, name="WF")]),
        ),
        patch.object(
            camp.db_client,
            "get_queued_runs_stats_for_campaigns",
            new=AsyncMock(return_value={}),
        ),
        patch.object(
            camp.db_client,
            "get_campaign_total_call_seconds_bulk",
            new=AsyncMock(return_value={1: 60, 2: 120}),
        ),
        patch.object(
            camp.db_client,
            "list_telephony_configurations",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(camp, "get_org_pricing", new=pricing),
    ):
        resp = await camp.get_campaigns(user=_user())
    assert resp.campaigns[0].spent_inr == 10.0  # 60s = 1 min * ₹10
    assert resp.campaigns[1].spent_inr == 20.0  # 120s = 2 min * ₹10
    pricing.assert_awaited_once_with(4)  # rate resolved ONCE for the list
