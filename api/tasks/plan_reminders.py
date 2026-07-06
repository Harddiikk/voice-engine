"""Daily cron: remind clients whose monthly plan is expiring / expired to renew.

Complements the in-app banner + auto-suspend by actually *sending* the client an
email. Idempotent per (expiry cycle, stage) so it emails at most once for the
5-day warning and once on expiry — a renewal resets the marker for the next
cycle. Best-effort: a missing SMTP config or a failed send just logs.
"""

from loguru import logger

from api.constants import UI_APP_URL
from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.admin.profile import (
    plan_reminder_already_sent,
    plan_state,
    record_plan_reminder,
)
from api.services.notifications.email import send_email


def _owner_email(org) -> str | None:
    if org is None:
        return None
    for u in getattr(org, "users", None) or []:
        if getattr(u, "email", None):
            return u.email
    return None


def _reminder_email(plan_title, days_left, expired, renew_url) -> tuple[str, str]:
    name = plan_title or "your plan"
    if expired:
        return (
            f"Your {name} has expired — renew to resume calling",
            f"Your {name} has expired and outbound calling is paused.\n\n"
            f"Renew now to resume immediately: {renew_url}\n",
        )
    unit = "day" if days_left == 1 else "days"
    return (
        f"Your {name} expires in {days_left} {unit} — please renew",
        f"Your {name} expires in {days_left} {unit}.\n\n"
        f"Renew now to avoid any interruption to your calling: {renew_url}\n",
    )


async def send_plan_renewal_reminders(ctx) -> dict:
    """Scan every client plan card and email a renewal reminder when due."""
    profiles = await db_client.get_all_configurations_by_key(
        OrganizationConfigurationKey.ADMIN_PROFILE.value
    )
    renew_url = f"{UI_APP_URL.rstrip('/')}/credits"
    sent = 0
    for row in profiles:
        org_id = row["organization_id"]
        profile = row.get("value") or {}
        state = plan_state(profile)
        # Only plan-card clients with an expiry that is within the warn window
        # or already expired.
        if not state["enabled"] or state["expires_at"] is None:
            continue
        if not (state["warn"] or state["expired"]):
            continue

        stage = "expired" if state["expired"] else "warn"
        expiry_iso = state["expires_at"].isoformat()
        if plan_reminder_already_sent(profile, stage, expiry_iso):
            continue

        org = await db_client.get_organization_with_users(org_id)
        owner = _owner_email(org)
        if not owner:
            continue

        card = state["card"] or {}
        subject, body = _reminder_email(
            card.get("title"), state["days_left"], state["expired"], renew_url
        )
        if await send_email(owner, subject, body):
            await record_plan_reminder(org_id, stage, expiry_iso)
            sent += 1

    if profiles:
        logger.info(
            f"plan-renewal-reminders: scanned {len(profiles)} client profiles, "
            f"sent {sent} reminder email(s)"
        )
    return {"scanned": len(profiles), "sent": sent}
