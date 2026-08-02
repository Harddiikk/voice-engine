"""campaigns.pause_reason (why a campaign auto-paused)

Revision ID: d3f8a1c05b27
Revises: c1a2b3d4e5f6
Create Date: 2026-08-02

Campaigns auto-pause for several distinct reasons — credits exhausted, the
per-campaign spend budget, a circuit-breaker trip, or an orchestrator failure —
and every one of them wrote the same ``state='paused'`` with no record of which.
Diagnosing a paused campaign therefore meant digging through container logs.

Additive-only: one nullable column, no backfill (existing paused campaigns
simply report an unknown reason). Safe to roll forward/back independently.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3f8a1c05b27"
down_revision: Union[str, None] = "c1a2b3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column("pause_reason", sa.String(length=48), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "pause_reason")
