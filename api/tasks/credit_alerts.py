"""Daily cron: warn clients before their calling credits run out.

Complements the hard 402 gate in ``quota_service`` by telling the client
*before* it bites. Without this, an org's first signal that it is out of credits
is calls failing mid-campaign, which reads as an outage rather than a bill.

Idempotent per (funding cycle, stage) so a daily run never spams: each of the
30-minute / 10-minute / empty stages emails at most once, and a top-up starts a
fresh cycle that re-arms them. Best-effort — a missing SMTP config or a failed
send just logs and leaves the stage unrecorded, so it retries tomorrow.

⚠️ NOT REGISTERED as a cron job. This sends email to real clients, so wiring it
into ``api/tasks/arq.py`` is a deliberate act by the operator, not a side effect
of deploying this file. To enable, add to ``WorkerSettings.cron_jobs``:

    cron(send_low_credit_alerts, hour={4}, minute={30}),
"""

from loguru import logger

from api.constants import UI_APP_URL
from api.db import db_client
from api.services.credit_alerts import (
    build_alert_email,
    credit_alert_already_sent,
    get_alert_context,
)
from api.services.credit_alerts import record_credit_alert
from api.services.admin.profile import get_admin_profile
from api.services.notifications.email import send_email


def _owner_email(org) -> str | None:
    if org is None:
        return None
    for u in getattr(org, "users", None) or []:
        if getattr(u, "email", None):
            return u.email
    return None


async def send_low_credit_alerts(ctx) -> dict:
    """Scan every metered org and email a low-balance warning when due."""
    organizations = await db_client.list_organizations_with_users()
    topup_url = f"{UI_APP_URL.rstrip('/')}/credits"
    scanned = 0
    sent = 0

    for org in organizations:
        scanned += 1
        # Unmetered orgs (NULL balance) return stage None and are skipped —
        # they have unlimited calling and must never be told they're low.
        context = await get_alert_context(org.id)
        stage = context.get("stage")
        if stage is None:
            continue

        profile = await get_admin_profile(org.id)
        cycle = context["cycle"]
        if credit_alert_already_sent(profile, stage, cycle):
            continue

        owner = _owner_email(org)
        if not owner:
            logger.warning(
                f"low-credit-alerts: org {org.id} is at stage '{stage}' but has "
                f"no user with an email address — cannot notify"
            )
            continue

        subject, body = build_alert_email(
            stage, context["remaining"] or 0, context.get("runway"), topup_url
        )
        if await send_email(owner, subject, body):
            await record_credit_alert(org.id, stage, cycle)
            sent += 1

    logger.info(
        f"low-credit-alerts: scanned {scanned} organizations, sent {sent} alert(s)"
    )
    return {"scanned": scanned, "sent": sent}
