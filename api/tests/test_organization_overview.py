"""Tests for the analytics-dashboard backend.

Covers three slices of the dashboard work:

1. ``GET /organizations/overview`` — route shaping, period param handling,
   and the unmetered-org case (mock ``db_client`` at the route module, same
   convention as ``test_admin_clients_credits``).
2. The pure disposition/success-rate helpers used by the overview aggregation.
3. The campaign-spend DB methods (authoritative completed-run duration SUM)
   and the ``call_type`` server-side filter.

The campaign *route* (``api.routes.campaign``) cannot be imported in this
environment — it transitively pulls the full pipecat provider stack
(azure / google / ...), which is not installed here — so the
``_build_campaign_response`` wiring is verified by the DB-method tests plus
inspection rather than a TestClient. The DB clients themselves import fine.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.db.campaign_client import CampaignClient
from api.db.filters import apply_workflow_run_filters
from api.db.models import WorkflowRunModel
from api.db.organization_usage_client import (
    classify_disposition,
    compute_success_rate,
)
from api.routes.organization_usage import (
    OrganizationOverviewResponse,
    router,
)
from api.services.auth.depends import get_user_with_selected_organization


# ======================================================================
# Overview route
# ======================================================================


def _sample_overview(**overrides):
    data = {
        "period": "month",
        "range": {
            "start": "2026-06-01T00:00:00+00:00",
            "end": "2026-07-03T00:00:00+00:00",
            "timezone": "UTC",
        },
        "totals": {
            "total_minutes": 56.4,
            "total_calls": 120,
            "connected_calls": 80,
            "success_rate": 75.0,
            "active_agents": 4,
            "live_calls": 2,
            "credits_seconds_remaining": 1800,
            "unlimited": False,
        },
        "trends": [{"bucket": "2026-06-01", "calls": 10, "minutes": 5.5}],
        "outcomes": {
            "success": 60,
            "failed": 20,
            "other": 40,
            "by_disposition": [{"disposition": "XFER", "count": 30}],
        },
    }
    data.update(overrides)
    return data


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_with_selected_organization] = lambda: (
        SimpleNamespace(id=1, selected_organization_id=7)
    )
    return app


def test_overview_returns_totals_trends_outcomes_shape():
    app = _make_app()
    client = TestClient(app)

    with patch("api.routes.organization_usage.db_client") as db:
        db.get_organization_overview = AsyncMock(return_value=_sample_overview())
        response = client.get("/organizations/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["period"] == "month"
    assert set(body["totals"]) >= {
        "total_minutes",
        "total_calls",
        "connected_calls",
        "success_rate",
        "active_agents",
        "live_calls",
        "credits_seconds_remaining",
        "unlimited",
    }
    assert body["trends"][0] == {"bucket": "2026-06-01", "calls": 10, "minutes": 5.5}
    assert body["outcomes"]["by_disposition"][0]["disposition"] == "XFER"
    # default period is month, and org id from the auth dependency is forwarded.
    db.get_organization_overview.assert_awaited_once_with(7, period="month")


def test_overview_defaults_to_month():
    app = _make_app()
    client = TestClient(app)

    with patch("api.routes.organization_usage.db_client") as db:
        db.get_organization_overview = AsyncMock(return_value=_sample_overview())
        response = client.get("/organizations/overview")

    assert response.status_code == 200
    _, kwargs = db.get_organization_overview.await_args
    assert kwargs["period"] == "month"


def test_overview_period_param_passed_through():
    app = _make_app()
    client = TestClient(app)

    with patch("api.routes.organization_usage.db_client") as db:
        db.get_organization_overview = AsyncMock(
            return_value=_sample_overview(period="week")
        )
        response = client.get("/organizations/overview?period=week")

    assert response.status_code == 200
    db.get_organization_overview.assert_awaited_once_with(7, period="week")


def test_overview_rejects_invalid_period():
    app = _make_app()
    client = TestClient(app)

    with patch("api.routes.organization_usage.db_client") as db:
        db.get_organization_overview = AsyncMock(return_value=_sample_overview())
        response = client.get("/organizations/overview?period=year")

    assert response.status_code == 422
    db.get_organization_overview.assert_not_awaited()


def test_overview_unmetered_org_returns_minutes_and_null_credits():
    """Unmetered org: credits null + unlimited true, but minutes still flow."""
    app = _make_app()
    client = TestClient(app)

    overview = _sample_overview()
    overview["totals"]["credits_seconds_remaining"] = None
    overview["totals"]["unlimited"] = True

    with patch("api.routes.organization_usage.db_client") as db:
        db.get_organization_overview = AsyncMock(return_value=overview)
        response = client.get("/organizations/overview")

    assert response.status_code == 200
    totals = response.json()["totals"]
    assert totals["credits_seconds_remaining"] is None
    assert totals["unlimited"] is True
    assert totals["total_minutes"] == 56.4


def test_overview_response_schema_validates_sample():
    # The exact dict the DB method returns must satisfy the response model.
    OrganizationOverviewResponse.model_validate(_sample_overview())


# ======================================================================
# Pure helpers: success-rate math + disposition classification
# ======================================================================


def test_success_rate_math():
    assert compute_success_rate(3, 1) == 75.0
    assert compute_success_rate(1, 0) == 100.0
    assert compute_success_rate(0, 5) == 0.0
    # No answered/success and no failed → 0, never a ZeroDivisionError.
    assert compute_success_rate(0, 0) == 0.0


def test_classify_disposition_buckets():
    # Success set is case-insensitive.
    assert classify_disposition("XFER") == "success"
    assert classify_disposition("Completed") == "success"
    assert classify_disposition("interested") == "success"
    # Failure set.
    assert classify_disposition("busy") == "failed"
    assert classify_disposition("no-answer") == "failed"
    assert classify_disposition("voicemail") == "failed"
    assert classify_disposition("failed") == "failed"
    # Missing / unknown → other (excluded from the success denominator).
    assert classify_disposition(None) == "other"
    assert classify_disposition("") == "other"
    assert classify_disposition("some_custom_code") == "other"


def test_classify_real_endtaskreason_dispositions():
    """The app's real EndTaskReason vocabulary must map sensibly — otherwise a
    campaign full of `user_qualified` calls shows 0% success (looks broken)."""
    assert classify_disposition("user_qualified") == "success"
    assert classify_disposition("call_transferred") == "success"
    assert classify_disposition("end_call_tool") == "success"
    assert classify_disposition("voicemail_detected") == "failed"
    assert classify_disposition("user_idle_max_duration_exceeded") == "failed"
    assert classify_disposition("system_connect_error") == "failed"
    assert classify_disposition("pipeline_error") == "failed"
    # Neutral: connected + system worked, but neither goal-hit nor tech-failure.
    assert classify_disposition("user_hangup") == "other"
    assert classify_disposition("user_disqualified") == "other"
    assert classify_disposition("call_duration_exceeded") == "other"


# ======================================================================
# Campaign spend (authoritative completed-run duration SUM)
# ======================================================================


class _FakeResult:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows


class _FakeSession:
    """Doubles as both the async context manager and the session."""

    def __init__(self, result):
        self._result = result

    async def execute(self, *args, **kwargs):
        return self._result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _campaign_client_returning(result) -> CampaignClient:
    client = CampaignClient.__new__(CampaignClient)
    client.async_session = MagicMock(return_value=_FakeSession(result))
    return client


@pytest.mark.asyncio
async def test_campaign_total_call_seconds_rounds_sum():
    client = _campaign_client_returning(_FakeResult(scalar=123.6))
    assert await client.get_campaign_total_call_seconds(42) == 124


@pytest.mark.asyncio
async def test_campaign_total_call_seconds_none_is_zero():
    client = _campaign_client_returning(_FakeResult(scalar=None))
    assert await client.get_campaign_total_call_seconds(42) == 0


@pytest.mark.asyncio
async def test_campaign_total_call_seconds_bulk_fills_missing_and_rounds():
    client = _campaign_client_returning(
        _FakeResult(rows=[(1, 100.4), (2, 59.6)])
    )
    out = await client.get_campaign_total_call_seconds_bulk([1, 2, 3])
    # Campaign 3 had no completed runs → 0; others rounded.
    assert out == {1: 100, 2: 60, 3: 0}


@pytest.mark.asyncio
async def test_campaign_total_call_seconds_bulk_empty_short_circuits():
    client = CampaignClient.__new__(CampaignClient)
    assert await client.get_campaign_total_call_seconds_bulk([]) == {}


# ======================================================================
# call_type server-side filter (powers the inbound call list)
# ======================================================================


def _compiled_sql(query) -> str:
    return str(query.compile(compile_kwargs={"literal_binds": True})).lower()


def test_call_type_filter_single_value():
    query = apply_workflow_run_filters(
        select(WorkflowRunModel),
        [{"attribute": "callType", "type": "text", "value": {"value": "inbound"}}],
    )
    sql = _compiled_sql(query)
    assert "'inbound'" in sql


def test_call_type_filter_raw_column_name_alias():
    query = apply_workflow_run_filters(
        select(WorkflowRunModel),
        [{"attribute": "call_type", "type": "text", "value": {"value": "outbound"}}],
    )
    assert "'outbound'" in _compiled_sql(query)


def test_call_type_filter_multiselect():
    query = apply_workflow_run_filters(
        select(WorkflowRunModel),
        [
            {
                "attribute": "callType",
                "type": "multiSelect",
                "value": {"codes": ["inbound", "outbound"]},
            }
        ],
    )
    sql = _compiled_sql(query)
    assert "'inbound'" in sql and "'outbound'" in sql


def test_call_type_filter_drops_invalid_direction():
    query = apply_workflow_run_filters(
        select(WorkflowRunModel),
        [{"attribute": "callType", "type": "text", "value": {"value": "sideways"}}],
    )
    # An unknown direction must not become a bound predicate.
    assert "'sideways'" not in _compiled_sql(query)
