"""Low-balance credit alerting — thresholds, runway, and send-once bookkeeping.

Plan *expiry* already warns clients before it bites (banner + auto-suspend +
``api/tasks/plan_reminders.py``). Credit *burn-down* did not: an org simply hit
zero and its calls started failing with 402 mid-campaign, which reads as an
outage rather than a billing event. This module supplies the decision logic for
a daily reminder that warns before that happens.

Everything here is pure except the two profile helpers at the bottom, so the
threshold and runway maths can be unit-tested without a database.

NOTE ON PERCENTAGES: ``organizations.free_call_seconds_remaining`` is a bare
counter with no "total granted" column, so a percentage-of-plan threshold is
not computable. Alerts therefore fire on absolute floors, and the *runway*
(days left at the current burn rate) carries the "how bad is this" signal.
"""

from typing import Optional

from api.db import db_client
from api.services.admin.profile import _save_admin_profile, get_admin_profile

# Ordered most- to least-severe. The stage name is what gets recorded as sent,
# so renaming one re-arms that alert for every org — treat these as stable ids.
CREDIT_ALERT_FLOORS: tuple[tuple[str, int], ...] = (
    ("empty", 0),
    ("10min", 10 * 60),
    ("30min", 30 * 60),
)

# Trailing window used to estimate the burn rate.
BURN_RATE_WINDOW_DAYS = 7


def select_alert_stage(remaining_seconds: Optional[int]) -> Optional[str]:
    """The most severe floor the balance has fallen to, or None.

    ``None`` remaining means an UNMETERED org (unlimited) — it must never be
    alerted, which is why this returns None rather than treating it as zero.
    """
    if remaining_seconds is None:
        return None
    for stage, floor in CREDIT_ALERT_FLOORS:
        if remaining_seconds <= floor:
            return stage
    return None


def estimate_runway_days(
    remaining_seconds: Optional[int], spent_seconds_in_window: int
) -> Optional[float]:
    """Days of calling left at the trailing burn rate, or None if unknowable.

    Returns None for an unmetered org, an already-empty balance, or an org that
    has spent nothing in the window (dividing by a zero burn rate would imply
    infinite runway, which is worse than saying nothing).
    """
    if remaining_seconds is None or remaining_seconds <= 0:
        return None
    if spent_seconds_in_window <= 0:
        return None
    per_day = spent_seconds_in_window / BURN_RATE_WINDOW_DAYS
    if per_day <= 0:
        return None
    return remaining_seconds / per_day


def format_minutes(seconds: int) -> str:
    """Human phrasing for a balance, e.g. '10 minutes' / 'about 1 hour'."""
    minutes = max(0, int(seconds // 60))
    if minutes >= 120:
        return f"about {minutes // 60} hours"
    if minutes >= 60:
        return "about 1 hour"
    if minutes == 1:
        return "1 minute"
    return f"{minutes} minutes"


def format_runway(days: Optional[float]) -> Optional[str]:
    """Phrase the runway, or None when it shouldn't be mentioned at all."""
    if days is None:
        return None
    if days < 1:
        return "less than a day at your current usage"
    if days < 2:
        return "about a day at your current usage"
    return f"about {int(round(days))} days at your current usage"


def build_alert_email(
    stage: str, remaining_seconds: int, runway: Optional[str], topup_url: str
) -> tuple[str, str]:
    """Subject + plain-text body for a low-balance alert."""
    signoff = "Thanks,\nTeam Auto4You\n"

    if stage == "empty":
        return (
            "Your calling credits have run out",
            "Hi,\n\n"
            "Your auto4you calling credits have run out, so outbound calls are "
            "paused for now. Any running campaign has been paused rather than "
            "left to fail, so nothing in your list has been lost.\n\n"
            f"Top up here and calling resumes immediately: {topup_url}\n\n"
            "If anything looks off, just reply to this email.\n\n" + signoff,
        )

    balance = format_minutes(remaining_seconds)
    runway_clause = f" — {runway}" if runway else ""
    return (
        f"You have {balance} of calling left",
        "Hi,\n\n"
        f"A quick heads-up: your auto4you balance is down to {balance}"
        f"{runway_clause}.\n\n"
        f"You can top up here so nothing pauses mid-campaign: {topup_url}\n\n"
        "If anything looks off, just reply to this email.\n\n" + signoff,
    )


# ---- send-once bookkeeping -------------------------------------------------
#
# Mirrors plan_reminder_already_sent / record_plan_reminder, but the "cycle" is
# the org's lifetime credited seconds instead of a plan expiry date. That value
# only moves when the org is credited, so a top-up starts a new cycle and
# re-arms every stage, while ordinary spend never does.


def credit_alert_already_sent(profile: dict, stage: str, cycle: int) -> bool:
    """True when this stage already fired for the current funding cycle."""
    sent = profile.get("credit_alerts_sent") or {}
    return sent.get("cycle") == cycle and stage in (sent.get("stages") or [])


async def record_credit_alert(organization_id: int, stage: str, cycle: int) -> None:
    """Mark a stage as sent for the current funding cycle."""
    profile = await get_admin_profile(organization_id)
    sent = profile.get("credit_alerts_sent") or {}
    if sent.get("cycle") != cycle:
        sent = {"cycle": cycle, "stages": []}
    if stage not in sent["stages"]:
        sent["stages"].append(stage)
    profile["credit_alerts_sent"] = sent
    await _save_admin_profile(organization_id, profile)


async def get_alert_context(organization_id: int) -> dict:
    """Everything the cron needs to decide and phrase one org's alert."""
    remaining = await db_client.get_free_call_seconds_remaining(organization_id)
    stage = select_alert_stage(remaining)
    if stage is None:
        return {"stage": None, "remaining": remaining}

    spent = await db_client.sum_spent_seconds(
        organization_id, since=_window_start()
    )
    cycle = await db_client.sum_credited_seconds(organization_id)
    return {
        "stage": stage,
        "remaining": remaining,
        "cycle": cycle,
        "runway": format_runway(estimate_runway_days(remaining, spent)),
    }


def _window_start():
    from datetime import UTC, datetime, timedelta

    return datetime.now(UTC) - timedelta(days=BURN_RATE_WINDOW_DAYS)
