"""Persist generated lesson content on knowledge nodes.

Revision ID: 0003_lesson_content
Revises: 0002_attempt_idempotency
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_lesson_content"
down_revision: str | None = "0002_attempt_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_nodes") as batch_op:
        batch_op.add_column(
            sa.Column("lesson_content", sa.Text(), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_nodes") as batch_op:
        batch_op.drop_column("lesson_content")
