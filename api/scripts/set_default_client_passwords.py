"""Backfill every non-owner org's stored VoiceLink client password to the
configured default (:data:`VOICELINK_DEFAULT_CLIENT_PASSWORD`, "12345678").

This standardizes the admin-panel *display* copy. It does NOT change the
password on VoiceLink's side (no change-password API): clients created from
now on provision with the default (a real match), but pre-existing VoiceLink
clients keep their original portal password until the owner also sets the
default in the VoiceLink portal.

    docker compose exec api python -m api.scripts.set_default_client_passwords --apply
"""

import argparse
import asyncio

from api.db import db_client
from api.services.auth.admin_emails import is_admin_email
from api.services.voicelink_clients.secrets import (
    decrypt_provision_secret,
    encrypt_provision_secret,
)
from api.services.voicelink_clients.service import (
    default_client_password,
    resolve_org_owner,
)


async def run(apply: bool) -> None:
    pw = default_client_password()
    print(f"default client password: {pw!r}")
    secret = encrypt_provision_secret(pw)
    if not secret:
        print("!! VOICELINK_PROVISION_KEY unset — cannot store secrets, aborting")
        return

    orgs = await db_client.list_organizations_with_users(exclude_user_id=-1)
    changed = 0
    skipped_owner = 0
    already = 0
    for org in orgs:
        owner = resolve_org_owner(org)
        if owner and is_admin_email(owner.email):
            skipped_owner += 1
            continue
        current = (
            decrypt_provision_secret(org.voicelink_provision_secret)
            if org.voicelink_provision_secret
            else None
        )
        if current == pw:
            already += 1
            continue
        label = owner.email if owner else "?"
        print(f"  org {org.id} ({label}): stored copy -> default")
        if apply:
            await db_client.update_organization_voicelink(
                org.id, provision_secret=secret
            )
        changed += 1

    verb = "updated" if apply else "would update"
    print(
        f"{verb} {changed} org(s); {already} already default; "
        f"{skipped_owner} owner org(s) skipped."
    )
    if not apply:
        print("(dry run — re-run with --apply to write)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write changes")
    args = parser.parse_args()
    asyncio.run(run(args.apply))


if __name__ == "__main__":
    main()
