"""credit ledger money core

Money-core hardening for the local call-seconds credit system:

- ``credit_ledger``: append-only ledger of every balance mutation
  (reserve / settle_release / settle_charge / leak_sweep / topup / grant /
  number_purchase / refund / adjustment), with an idempotency key so a
  retried mutation can never double-apply.
- ``workflow_runs.reserved_credit_seconds`` + ``credits_settled_at``: the
  reservation hold moves out of ``initial_context`` JSON into real columns;
  ``credits_settled_at`` is the settlement CAS marker (set exactly once).
- Partial index over unsettled holds so the leak sweeper's scan stays cheap.
- ``organizations.voicelink_did_purchased_at``: reserved for the upcoming
  DID-purchase workstream (added here so one migration covers both).

Revision ID: b7c4d2e8a915
Revises: 754feb4556ce
Create Date: 2026-07-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c4d2e8a915"
down_revision: Union[str, None] = "754feb4556ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("delta_seconds", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=True),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id"),
            nullable=True,
        ),
        sa.Column(
            "campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=True
        ),
        sa.Column(
            "payment_transaction_id",
            sa.Integer(),
            sa.ForeignKey("payment_transactions.id"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_credit_ledger_organization_id", "credit_ledger", ["organization_id"]
    )
    op.create_index(
        "ix_credit_ledger_workflow_run_id", "credit_ledger", ["workflow_run_id"]
    )
    op.create_index(
        "ix_credit_ledger_org_created",
        "credit_ledger",
        ["organization_id", "created_at"],
    )
    op.create_unique_constraint(
        "uq_credit_ledger_idempotency_key", "credit_ledger", ["idempotency_key"]
    )

    op.add_column(
        "workflow_runs",
        sa.Column("reserved_credit_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("credits_settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_workflow_runs_unsettled_holds",
        "workflow_runs",
        ["created_at"],
        postgresql_where=sa.text(
            "reserved_credit_seconds IS NOT NULL AND credits_settled_at IS NULL"
        ),
    )

    op.add_column(
        "organizations",
        sa.Column(
            "voicelink_did_purchased_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "voicelink_did_purchased_at")

    op.drop_index("ix_workflow_runs_unsettled_holds", "workflow_runs")
    op.drop_column("workflow_runs", "credits_settled_at")
    op.drop_column("workflow_runs", "reserved_credit_seconds")

    op.drop_index("ix_credit_ledger_org_created", "credit_ledger")
    op.drop_index("ix_credit_ledger_workflow_run_id", "credit_ledger")
    op.drop_index("ix_credit_ledger_organization_id", "credit_ledger")
    op.drop_table("credit_ledger")
