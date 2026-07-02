"""VoiceLink reseller client-management service.

Creates VoiceLink "clients" (sub-accounts under our reseller account) via
``POST /v1/reseller/client/create``. Signup only stashes an encrypted copy
of the platform password (``stash_voicelink_signup_secret``); the client is
provisioned lazily by ``ensure_voicelink_client`` at first need (KYC entry,
number purchase) or from the admin Clients view. The outcome is stored on
the organization (``voicelink_client_id`` / ``voicelink_username`` /
``voicelink_status`` / ``voicelink_error``).
"""

from .client import (
    VoiceLinkClientError,
    VoiceLinkClientsClient,
    get_voicelink_clients_client,
)
from .service import (
    derive_username,
    ensure_voicelink_client,
    generate_client_password,
    provision_voicelink_client,
    resolve_org_owner,
    split_signup_name,
    stash_voicelink_signup_secret,
)

__all__ = [
    "VoiceLinkClientError",
    "VoiceLinkClientsClient",
    "get_voicelink_clients_client",
    "derive_username",
    "ensure_voicelink_client",
    "generate_client_password",
    "provision_voicelink_client",
    "resolve_org_owner",
    "split_signup_name",
    "stash_voicelink_signup_secret",
]
