"""How many calls a campaign is allowed to run at once.

Four ceilings stack, and the smallest one wins:

1. ``max_concurrency`` stored on the campaign (Advanced Settings). Campaigns
   that never set it fall back to ``DEFAULT_CAMPAIGN_MAX_CONCURRENCY``.
2. The organization's ``CONCURRENT_CALL_LIMIT`` configuration.
3. The trunk's CHANNEL capacity — ``max_concurrent_calls`` on the campaign's
   telephony configuration. One caller-id carries as many simultaneous calls
   as the trunk has channels, so the number COUNT is not the bound.
4. An *adaptive throttle* the circuit breaker applies when calls start
   failing. Rather than pausing the campaign, we halve its concurrency and
   keep dialing, then climb back one slot at a time as calls succeed. See
   ``apply_throttle`` / ``recover_throttle``.

(3) is the one that used to be missing at runtime: creation-time validation
rejected a `max_concurrency` above the channel capacity, but the dispatcher
then dialed at ``min(campaign, org_limit)`` and ignored the trunk entirely. A
campaign with no stored `max_concurrency` on a 4-channel trunk therefore ran
at the org limit of 5, and the carrier rejected the 5th call. Everything now
resolves through here so the number a user is allowed to save is the number
we actually dial at.
"""

from datetime import UTC, datetime

from loguru import logger

from api.constants import (
    DEFAULT_CAMPAIGN_MAX_CONCURRENCY,
    DEFAULT_ORG_CONCURRENCY_LIMIT,
    TELEPHONY_DEFAULT_MAX_CONCURRENT_CALLS,
)
from api.db import db_client
from api.enums import OrganizationConfigurationKey

# Key under campaigns.orchestrator_metadata holding the adaptive throttle.
THROTTLE_KEY = "concurrency_throttle"


async def get_org_concurrent_limit(organization_id: int) -> int:
    """The organization's configured concurrent-call ceiling."""
    try:
        config = await db_client.get_configuration(
            organization_id,
            OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value,
        )
        if config and config.value:
            return int(config.value.get("value", DEFAULT_ORG_CONCURRENCY_LIMIT))
    except Exception as e:
        logger.warning(
            f"Error reading concurrent limit for org {organization_id}: {e}"
        )
    return DEFAULT_ORG_CONCURRENCY_LIMIT


async def get_channel_capacity(
    organization_id: int, telephony_configuration_id: int | None = None
) -> int:
    """Concurrent-call capacity (trunk CHANNELS) of a dialing configuration.

    Returns 0 when the configuration has no active numbers at all, which
    callers treat as "unknown — don't cap", matching the previous behaviour.
    """
    try:
        cfg = None
        if telephony_configuration_id is not None:
            cfg = await db_client.get_telephony_configuration_for_org(
                telephony_configuration_id, organization_id
            )
        if cfg is None:
            cfg = await db_client.get_default_telephony_configuration(organization_id)
        if cfg:
            addresses = await db_client.list_active_normalized_addresses_for_config(
                cfg.id
            )
            if not addresses:
                return 0
            raw = (cfg.credentials or {}).get("max_concurrent_calls")
            return int(raw) if raw else TELEPHONY_DEFAULT_MAX_CONCURRENT_CALLS
    except Exception as e:
        logger.warning(
            f"Error reading channel capacity for org {organization_id} "
            f"config {telephony_configuration_id}: {e}"
        )
    return 0


async def get_effective_limit(
    organization_id: int, telephony_configuration_id: int | None = None
) -> int:
    """The highest concurrency a campaign on this trunk may be set to."""
    org_limit = await get_org_concurrent_limit(organization_id)
    channel_capacity = await get_channel_capacity(
        organization_id, telephony_configuration_id
    )
    return min(org_limit, channel_capacity) if channel_capacity > 0 else org_limit


def get_configured_concurrency(campaign) -> int:
    """What the campaign ASKS for, before org/trunk/throttle ceilings apply."""
    campaign_max = None
    if campaign.orchestrator_metadata:
        campaign_max = campaign.orchestrator_metadata.get("max_concurrency")
    if campaign_max is None:
        campaign_max = DEFAULT_CAMPAIGN_MAX_CONCURRENCY
    return max(1, int(campaign_max))


def get_throttle(campaign) -> dict | None:
    """The active adaptive throttle, or None when the campaign isn't throttled."""
    if not campaign.orchestrator_metadata:
        return None
    throttle = campaign.orchestrator_metadata.get(THROTTLE_KEY)
    if isinstance(throttle, dict) and throttle.get("value"):
        return throttle
    return None


async def resolve_campaign_concurrency(campaign) -> int:
    """How many simultaneous calls this campaign may actually dial right now.

    Never returns less than 1 — a campaign that resolved to 0 would wait for a
    slot forever instead of dialing.
    """
    ceilings = [get_configured_concurrency(campaign)]

    ceilings.append(
        await get_effective_limit(
            campaign.organization_id,
            getattr(campaign, "telephony_configuration_id", None),
        )
    )

    throttle = get_throttle(campaign)
    if throttle:
        ceilings.append(int(throttle["value"]))

    return max(1, min(ceilings))


async def _write_throttle(campaign_id: int, throttle: dict | None) -> bool:
    """Read-modify-write the throttle onto the campaign's metadata.

    Single-writer in practice (one ARQ worker), and a lost update here only
    costs one adjustment step — the breaker re-evaluates on the next outcome.
    """
    try:
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            return False
        metadata = dict(campaign.orchestrator_metadata or {})
        if throttle is None:
            metadata.pop(THROTTLE_KEY, None)
        else:
            metadata[THROTTLE_KEY] = throttle
        await db_client.update_campaign(
            campaign_id=campaign_id, orchestrator_metadata=metadata
        )
        return True
    except Exception as e:
        logger.error(f"Failed writing concurrency throttle for campaign {campaign_id}: {e}")
        return False


async def apply_throttle(campaign, reason: str) -> int | None:
    """Halve the campaign's concurrency so it can keep running.

    Returns the new value, or None when it is already down to a single call —
    the signal to the caller that backing off further isn't possible and the
    campaign has to be paused instead.
    """
    current = await resolve_campaign_concurrency(campaign)
    if current <= 1:
        return None

    new_value = max(1, current // 2)
    if new_value >= current:  # defensive; halving should always reduce
        return None

    ok = await _write_throttle(
        campaign.id,
        {
            "value": new_value,
            "reason": reason,
            "previous": current,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    return new_value if ok else None


async def recover_throttle(campaign) -> int | None:
    """Step a throttled campaign back up by one slot.

    Returns the new ceiling, or None when there was nothing to recover. The
    throttle is removed entirely once it reaches what the campaign asked for,
    so a recovered campaign looks exactly like one that never degraded.
    """
    throttle = get_throttle(campaign)
    if not throttle:
        return None

    target = get_configured_concurrency(campaign)
    current = int(throttle["value"])
    if current >= target:
        await _write_throttle(campaign.id, None)
        return target

    new_value = current + 1
    if new_value >= target:
        await _write_throttle(campaign.id, None)
        return target

    ok = await _write_throttle(
        campaign.id,
        {
            **throttle,
            "value": new_value,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    return new_value if ok else None


async def clear_throttle(campaign_id: int) -> None:
    """Drop any throttle — used when the user sets concurrency by hand."""
    await _write_throttle(campaign_id, None)
