"""Admin audit log helper — record every superuser mutation on a client org.

Best-effort: an audit-write failure must never break the underlying admin
action, so callers wrap or the helper swallows.
"""

from typing import Optional

from loguru import logger

from api.db import db_client


async def record_admin_action(
    *,
    actor_user_id: Optional[int],
    target_organization_id: Optional[int],
    action: str,
    detail: Optional[dict] = None,
) -> None:
    """Append an admin-audit row. Never raises."""
    try:
        await db_client.create_admin_audit(
            actor_user_id=actor_user_id,
            target_organization_id=target_organization_id,
            action=action,
            detail=detail,
        )
    except Exception as exc:  # pragma: no cover - audit must not block the action
        logger.warning(
            f"Failed to write admin audit ({action} on org "
            f"{target_organization_id}): {exc}"
        )
