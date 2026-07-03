"""DB access for the admin audit log (superuser action history)."""

from typing import Any, List, Optional

from sqlalchemy import select

from api.db.base_client import BaseDBClient
from api.db.models import AdminAuditModel


class AdminAuditClient(BaseDBClient):
    async def create_admin_audit(
        self,
        *,
        actor_user_id: Optional[int],
        target_organization_id: Optional[int],
        action: str,
        detail: Optional[dict] = None,
    ) -> AdminAuditModel:
        async with self.async_session() as session:
            row = AdminAuditModel(
                actor_user_id=actor_user_id,
                target_organization_id=target_organization_id,
                action=action,
                detail=detail,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_admin_audit(
        self,
        *,
        target_organization_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AdminAuditModel]:
        async with self.async_session() as session:
            query = select(AdminAuditModel).order_by(
                AdminAuditModel.created_at.desc(), AdminAuditModel.id.desc()
            )
            if target_organization_id is not None:
                query = query.where(
                    AdminAuditModel.target_organization_id == target_organization_id
                )
            query = query.limit(min(max(1, limit), 500)).offset(max(0, offset))
            result = await session.execute(query)
            return list(result.scalars().all())
