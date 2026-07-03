"""admin_audit table (superuser action log)

Revision ID: c1a2b3d4e5f6
Revises: b7c4d2e8a915
Create Date: 2026-07-03

Additive-only: a new append-only audit table. No changes to existing tables,
so it is safe to roll forward/back independently.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1a2b3d4e5f6"
down_revision: Union[str, None] = "b7c4d2e8a915"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_admin_audit_id", "admin_audit", ["id"])
    op.create_index(
        "ix_admin_audit_actor_user_id", "admin_audit", ["actor_user_id"]
    )
    op.create_index(
        "ix_admin_audit_target_organization_id",
        "admin_audit",
        ["target_organization_id"],
    )
    op.create_index(
        "ix_admin_audit_org_created",
        "admin_audit",
        ["target_organization_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_admin_audit_org_created", table_name="admin_audit")
    op.drop_index(
        "ix_admin_audit_target_organization_id", table_name="admin_audit"
    )
    op.drop_index("ix_admin_audit_actor_user_id", table_name="admin_audit")
    op.drop_index("ix_admin_audit_id", table_name="admin_audit")
    op.drop_table("admin_audit")
