"""Plan renewal reminder cron: sends once per (expiry, stage), skips
non-plan/off-window, honours idempotency."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.tasks import plan_reminders

CARD = {"title": "Enterprise", "price_inr": 25000, "enabled": True}


def _profile(days_from_now, reminders_sent=None):
    p = {
        "plan_card": CARD,
        "plan_expires_at": (datetime.now(UTC) + timedelta(days=days_from_now)).isoformat(),
    }
    if reminders_sent:
        p["plan_reminders_sent"] = reminders_sent
    return p


def _org(email="client@x.test"):
    return SimpleNamespace(users=[SimpleNamespace(email=email)])


async def _run(profiles, email_ok=True):
    with (
        patch.object(plan_reminders.db_client, "get_all_configurations_by_key", new=AsyncMock(return_value=profiles)),
        patch.object(plan_reminders.db_client, "get_organization_with_users", new=AsyncMock(return_value=_org())),
        patch.object(plan_reminders, "send_email", new=AsyncMock(return_value=email_ok)) as send,
        patch.object(plan_reminders, "record_plan_reminder", new=AsyncMock()) as rec,
    ):
        result = await plan_reminders.send_plan_renewal_reminders(ctx={})
    return result, send, rec


@pytest.mark.asyncio
async def test_sends_warning_within_5_days():
    result, send, rec = await _run([{"organization_id": 4, "value": _profile(3)}])
    assert result["sent"] == 1
    send.assert_awaited_once()
    subj = send.await_args.args[1]
    assert "expires in" in subj and "please renew" in subj
    rec.assert_awaited_once()
    assert rec.await_args.args[0] == 4 and rec.await_args.args[1] == "warn"


@pytest.mark.asyncio
async def test_sends_expired_notice():
    result, send, _ = await _run([{"organization_id": 4, "value": _profile(-1)}])
    assert result["sent"] == 1
    assert "expired" in send.await_args.args[1].lower()


@pytest.mark.asyncio
async def test_skips_when_more_than_5_days_out():
    result, send, _ = await _run([{"organization_id": 4, "value": _profile(20)}])
    assert result["sent"] == 0
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_already_reminded_this_cycle():
    prof = _profile(3)
    expiry = prof["plan_expires_at"]
    prof["plan_reminders_sent"] = {"expiry": expiry, "stages": ["warn"]}
    result, send, _ = await _run([{"organization_id": 4, "value": prof}])
    assert result["sent"] == 0
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_card_is_ignored():
    result, send, _ = await _run([{"organization_id": 4, "value": {"suspended": False}}])
    assert result["sent"] == 0
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_marker_not_recorded_when_email_fails():
    _, send, rec = await _run([{"organization_id": 4, "value": _profile(2)}], email_ok=False)
    send.assert_awaited_once()
    rec.assert_not_awaited()  # retry tomorrow if SMTP was down
