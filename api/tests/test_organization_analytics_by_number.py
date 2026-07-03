"""Tests for the per-number ("By Number" / ports) analytics slice.

Two layers, mirroring the conventions already used in
``test_organization_overview.py`` (route shaping via a mocked ``db_client``)
and the campaign DB-method tests (a fake async session):

1. ``GET /organizations/analytics/by-number`` — route shaping, org-scoping
   (the org id comes from the auth dependency, never the request), and the
   optional ``start_date`` / ``end_date`` / ``call_type`` passthrough.
2. ``OrganizationUsageClient.get_organization_analytics_by_number`` — the
   Python-side re-aggregation: grouping rows to the per-number level, the
   shared success-rate/disposition mapping, the bare-digit label join, and
   the calls-desc ordering.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.db.organization_usage_client import OrganizationUsageClient
from api.routes.organization_usage import (
    ByNumberAnalyticsResponse,
    router,
)
from api.services.auth.depends import get_user_with_selected_organization


# ======================================================================
# Route: GET /organizations/analytics/by-number
# ======================================================================


def _sample_numbers():
    return [
        {
            "number": "15551230000",
            "label": "Sales line",
            "calls": 40,
            "connected": 30,
            "success_rate": 66.7,
            "avg_duration_seconds": 84.2,
            "total_minutes": 42.1,
            "top_dispositions": [
                {"disposition": "user_qualified", "count": 20},
                {"disposition": "user_hangup", "count": 10},
            ],
        },
        {
            "number": "919911848000",
            "label": "919911848000",
            "calls": 12,
            "connected": 5,
            "success_rate": 0.0,
            "avg_duration_seconds": 12.0,
            "total_minutes": 1.0,
            "top_dispositions": [],
        },
    ]


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_with_selected_organization] = lambda: (
        SimpleNamespace(id=1, selected_organization_id=7)
    )
    return app


def test_by_number_returns_rows_and_forwards_org_id():
    app = _make_app()
    client = TestClient(app)

    with patch("api.routes.organization_usage.db_client") as db:
        db.get_organization_analytics_by_number = AsyncMock(
            return_value=_sample_numbers()
        )
        response = client.get("/organizations/analytics/by-number")

    assert response.status_code == 200
    body = response.json()
    assert [n["number"] for n in body["numbers"]] == ["15551230000", "919911848000"]
    first = body["numbers"][0]
    assert set(first) >= {
        "number",
        "label",
        "calls",
        "connected",
        "success_rate",
        "avg_duration_seconds",
        "total_minutes",
        "top_dispositions",
    }
    assert first["top_dispositions"][0]["disposition"] == "user_qualified"
    # Org id is taken from the auth dependency (tenant isolation), not the request.
    args, kwargs = db.get_organization_analytics_by_number.await_args
    assert args[0] == 7
    assert kwargs["start_date"] is None
    assert kwargs["end_date"] is None
    assert kwargs["call_type"] is None


def test_by_number_passes_through_filters():
    app = _make_app()
    client = TestClient(app)

    with patch("api.routes.organization_usage.db_client") as db:
        db.get_organization_analytics_by_number = AsyncMock(return_value=[])
        response = client.get(
            "/organizations/analytics/by-number"
            "?start_date=2026-06-01T00:00:00Z&end_date=2026-07-01T00:00:00Z"
            "&call_type=outbound"
        )

    assert response.status_code == 200
    assert response.json() == {"numbers": []}
    _, kwargs = db.get_organization_analytics_by_number.await_args
    assert kwargs["start_date"] is not None
    assert kwargs["end_date"] is not None
    assert kwargs["call_type"] == "outbound"


def test_by_number_rejects_invalid_call_type():
    app = _make_app()
    client = TestClient(app)

    with patch("api.routes.organization_usage.db_client") as db:
        db.get_organization_analytics_by_number = AsyncMock(return_value=[])
        response = client.get(
            "/organizations/analytics/by-number?call_type=sideways"
        )

    assert response.status_code == 422
    db.get_organization_analytics_by_number.assert_not_awaited()


def test_by_number_response_schema_validates_sample():
    ByNumberAnalyticsResponse.model_validate({"numbers": _sample_numbers()})


# ======================================================================
# DB method: per-number re-aggregation + label join
# ======================================================================


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _SeqSession:
    """Returns queued results in call order; doubles as its own CM."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, *args, **kwargs):
        return self._results.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _client_returning(grouped_rows, phone_rows) -> OrganizationUsageClient:
    client = OrganizationUsageClient.__new__(OrganizationUsageClient)
    client.async_session = MagicMock(
        return_value=_SeqSession([_Result(grouped_rows), _Result(phone_rows)])
    )
    return client


@pytest.mark.asyncio
async def test_by_number_reaggregates_and_labels():
    # Two DIDs. The first has three disposition groups (success / failed / other)
    # so success_rate = success / (success + failed) = 6 / (6 + 3) = 66.7.
    grouped = [
        SimpleNamespace(
            number="15551230000",
            disposition="user_qualified",
            calls=6,
            seconds=360.0,
            connected=6,
        ),
        SimpleNamespace(
            number="15551230000",
            disposition="voicemail_detected",
            calls=3,
            seconds=0.0,
            connected=0,
        ),
        SimpleNamespace(
            number="15551230000",
            disposition="user_hangup",
            calls=1,
            seconds=30.0,
            connected=1,
        ),
        SimpleNamespace(
            number="919911848000",
            disposition=None,
            calls=2,
            seconds=40.0,
            connected=2,
        ),
    ]
    # address_normalized carries +country formatting; must still match the bare DID.
    phones = [
        SimpleNamespace(
            address_normalized="+1 (555) 123-0000",
            address="+15551230000",
            label="Sales line",
        ),
    ]
    client = _client_returning(grouped, phones)

    out = await client.get_organization_analytics_by_number(7)

    # Ordered by calls desc: 10 (first DID) then 2.
    assert [n["number"] for n in out] == ["15551230000", "919911848000"]

    first = out[0]
    assert first["label"] == "Sales line"  # bare-digit join matched the +formatted addr
    assert first["calls"] == 10
    assert first["connected"] == 7
    assert first["success_rate"] == 66.7
    # Avg duration is over connected calls: (360 + 0 + 30) / 7 = 55.7.
    assert first["avg_duration_seconds"] == 55.7
    assert first["total_minutes"] == 6.5
    assert first["top_dispositions"][0] == {
        "disposition": "user_qualified",
        "count": 6,
    }

    second = out[1]
    # No configured label → falls back to the bare number; NULL disposition dropped.
    assert second["label"] == "919911848000"
    assert second["top_dispositions"] == []


@pytest.mark.asyncio
async def test_by_number_skips_runs_without_a_did():
    grouped = [
        SimpleNamespace(
            number="", disposition="user_qualified", calls=5, seconds=100.0, connected=5
        ),
    ]
    client = _client_returning(grouped, [])
    out = await client.get_organization_analytics_by_number(7)
    assert out == []
