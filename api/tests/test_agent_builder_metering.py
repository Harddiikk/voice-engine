"""Agent-builder /generate metering: rate-limit, credit pre-check, flat charge."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes import agent_builder


def _user(org=4):
    return SimpleNamespace(id=7, selected_organization_id=org, provider_id="prov_7")


def _req():
    return SimpleNamespace(prompt="build me a sales agent")


@pytest.mark.asyncio
async def test_charges_flat_credit_after_success(monkeypatch):
    monkeypatch.setattr(agent_builder, "AGENT_BUILD_CREDIT_SECONDS", 60)
    monkeypatch.setattr(agent_builder, "enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(agent_builder, "capture_event", lambda **k: None)
    gen = AsyncMock(return_value={"workflow_id": 11, "name": "Sales", "status": "draft", "editor_path": "/workflow/1"})
    monkeypatch.setattr(agent_builder.generator, "generate_agent", gen)
    charge = AsyncMock(return_value=1000)
    with (
        patch.object(agent_builder.db_client, "get_free_call_seconds_remaining", new=AsyncMock(return_value=5000)),
        patch.object(agent_builder.db_client, "charge_purchase_tx", new=charge),
    ):
        resp = await agent_builder.generate_agent(request=_req(), user=_user())

    assert resp.workflow_id == 11
    charge.assert_awaited_once()
    kwargs = charge.await_args.kwargs
    assert kwargs["kind"] == "agent_build"
    assert kwargs["idempotency_key"] == "agentbuild:11"
    assert charge.await_args.args[1] == 60  # seconds charged


@pytest.mark.asyncio
async def test_402_when_insufficient_balance_pre_check(monkeypatch):
    monkeypatch.setattr(agent_builder, "AGENT_BUILD_CREDIT_SECONDS", 60)
    monkeypatch.setattr(agent_builder, "enforce_rate_limit", AsyncMock())
    gen = AsyncMock()
    monkeypatch.setattr(agent_builder.generator, "generate_agent", gen)
    with patch.object(
        agent_builder.db_client,
        "get_free_call_seconds_remaining",
        new=AsyncMock(return_value=30),  # < 60 cost
    ):
        with pytest.raises(HTTPException) as exc:
            await agent_builder.generate_agent(request=_req(), user=_user())

    assert exc.value.status_code == 402
    gen.assert_not_awaited()  # never spent LLM tokens


@pytest.mark.asyncio
async def test_unmetered_org_generates_free(monkeypatch):
    monkeypatch.setattr(agent_builder, "AGENT_BUILD_CREDIT_SECONDS", 60)
    monkeypatch.setattr(agent_builder, "enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(agent_builder, "capture_event", lambda **k: None)
    monkeypatch.setattr(
        agent_builder.generator,
        "generate_agent",
        AsyncMock(return_value={"workflow_id": 12, "name": "X", "status": "draft", "editor_path": "/workflow/1"}),
    )
    charge = AsyncMock(return_value="unmetered")
    with (
        # NULL balance = unlimited -> pre-check skipped, charge no-ops.
        patch.object(agent_builder.db_client, "get_free_call_seconds_remaining", new=AsyncMock(return_value=None)),
        patch.object(agent_builder.db_client, "charge_purchase_tx", new=charge),
    ):
        resp = await agent_builder.generate_agent(request=_req(), user=_user())

    assert resp.workflow_id == 12  # generated despite unlimited (no 402)


@pytest.mark.asyncio
async def test_no_charge_when_cost_zero(monkeypatch):
    monkeypatch.setattr(agent_builder, "AGENT_BUILD_CREDIT_SECONDS", 0)
    monkeypatch.setattr(agent_builder, "enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(agent_builder, "capture_event", lambda **k: None)
    monkeypatch.setattr(
        agent_builder.generator,
        "generate_agent",
        AsyncMock(return_value={"workflow_id": 13, "name": "Y", "status": "draft", "editor_path": "/workflow/1"}),
    )
    charge = AsyncMock()
    with patch.object(agent_builder.db_client, "charge_purchase_tx", new=charge):
        resp = await agent_builder.generate_agent(request=_req(), user=_user())

    assert resp.workflow_id == 13
    charge.assert_not_awaited()  # free when cost is 0 (OSS default)


@pytest.mark.asyncio
async def test_rate_limit_blocks_before_generation(monkeypatch):
    monkeypatch.setattr(
        agent_builder,
        "enforce_rate_limit",
        AsyncMock(side_effect=HTTPException(status_code=429, detail="slow down")),
    )
    gen = AsyncMock()
    monkeypatch.setattr(agent_builder.generator, "generate_agent", gen)
    with pytest.raises(HTTPException) as exc:
        await agent_builder.generate_agent(request=_req(), user=_user())
    assert exc.value.status_code == 429
    gen.assert_not_awaited()
