"""Add adaptive review session metadata.

Revision ID: 0006_adaptive_review_sessions
Revises: 0005_background_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_adaptive_review_sessions"
down_revision: str | None = "0005_background_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为会话添加学习/复习类型和动态练习外键，并建立类型约束。"""

    with op.batch_alter_table("study_sessions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "kind",
                sa.String(length=30),
                nullable=False,
                server_default="learning",
            )
        )
        batch_op.add_column(sa.Column("exercise_id", sa.String(length=36)))
        batch_op.create_foreign_key(
            "fk_study_sessions_exercise_id",
            "exercises",
            ["exercise_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "study_session_kind", "kind IN ('learning', 'review')"
        )


def downgrade() -> None:
    """移除自适应复习会话增加的约束、外键和字段。"""

    with op.batch_alter_table("study_sessions") as batch_op:
        batch_op.drop_constraint("study_session_kind", type_="check")
        batch_op.drop_constraint(
            "fk_study_sessions_exercise_id", type_="foreignkey"
        )
        batch_op.drop_column("exercise_id")
        batch_op.drop_column("kind")
