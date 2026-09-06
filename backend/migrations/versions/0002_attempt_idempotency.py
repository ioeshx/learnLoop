"""Add attempt idempotency protection.

Revision ID: 0002_attempt_idempotency
Revises: 0001_initial_schema
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_attempt_idempotency"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("mastery_events") as batch_op:
        batch_op.create_unique_constraint(
            "uq_mastery_events_attempt_id", ["attempt_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("mastery_events") as batch_op:
        batch_op.drop_constraint("uq_mastery_events_attempt_id", type_="unique")
