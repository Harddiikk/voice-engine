"""How many calls a campaign is allowed to run at once.

Three ceilings stack, and the smallest one wins:

1. ``max_concurrency`` stored on the campaign (Advanced Settings). Campaigns
   that never set it fall back to ``DEFAULT_CAMPAIGN_MAX_CONCURRENCY``.
2. The organization's ``CONCURRENT_CALL_LIMIT`` configuration.
3. The trunk's CHANNEL capacity — ``max_concurrent_calls`` on the campaign's
   telephony configuration. One caller-id carries as many simultaneous calls
   as the trunk has channels, so the number COUNT is not the bound.

(3) is the one that used to be missing at runtime: creation-time validation
rejected a `max_concurrency` above the channel capacity, but the dispatcher
then dialed at ``min(campaign, org_limit)`` and ignored the trunk entirely. A
campaign with no stored `max_concurrency` on a 4-channel trunk therefore ran
at the org limit of 5, and the carrier rejected the 5th call. Everything now
resolves through here so the number a user is allowed to save is the number
we actually dial at.
"""

from loguru import logger

from api.constants import (
    DEFAULT_CAMPAIGN_MAX_CONCURRENCY,
    DEFAULT_ORG_CONCURRENCY_LIMIT,
    TELEPHONY_DEFAULT_MAX_CONCURRENT_CALLS,
)
from api.db import db_client
from api.enums import OrganizationConfigurationKey


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


async def resolve_campaign_concurrency(campaign) -> int:
    """How many simultaneous calls this campaign may actually dial.

    Never returns less than 1 — a campaign that resolved to 0 would wait for a
    slot forever instead of dialing.
    """
    campaign_max = None
    if campaign.orchestrator_metadata:
        campaign_max = campaign.orchestrator_metadata.get("max_concurrency")
    if campaign_max is None:
        campaign_max = DEFAULT_CAMPAIGN_MAX_CONCURRENCY

    effective_limit = await get_effective_limit(
        campaign.organization_id,
        getattr(campaign, "telephony_configuration_id", None),
    )
    return max(1, min(int(campaign_max), effective_limit))
