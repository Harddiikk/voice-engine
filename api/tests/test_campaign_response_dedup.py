"""_build_campaign_response should pass through a duplicates_removed count
when the caller supplies one, and default to None otherwise.

_build_campaign_response is synchronous (no I/O inside it) — these are
plain, non-async tests.

``api.routes.campaign`` can't be imported directly in the test venv — its
``campaign.runner`` import transitively pulls pipecat azure/google speech deps
that aren't installed. We stub that one hub module before import; everything
else under test (``_build_campaign_response``) is the real code.
"""

import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace

# Stub the azure/google-heavy runner hub so campaign.py imports in this venv.
if "api.services.campaign.runner" not in sys.modules:
    _runner_stub = types.ModuleType("api.services.campaign.runner")
    _runner_stub.campaign_runner_service = object()
    sys.modules["api.services.campaign.runner"] = _runner_stub

from api.routes.campaign import _build_campaign_response  # noqa: E402


def _fake_campaign():
    return SimpleNamespace(
        id=1,
        name="Test Campaign",
        workflow_id=1,
        state="draft",
        source_type="csv",
        source_id="contacts.csv",
        total_rows=None,
        processed_rows=0,
        failed_rows=0,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=None,
        retry_config=None,
        orchestrator_metadata={},
        telephony_configuration_id=None,
        logs=[],
        organization_id=1,
    )


def test_duplicates_removed_defaults_to_none():
    response = _build_campaign_response(_fake_campaign(), "Test Workflow")
    assert response.duplicates_removed is None


def test_duplicates_removed_is_threaded_through_when_provided():
    response = _build_campaign_response(
        _fake_campaign(), "Test Workflow", duplicates_removed=12
    )
    assert response.duplicates_removed == 12
