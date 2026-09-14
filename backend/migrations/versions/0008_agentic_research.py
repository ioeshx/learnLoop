"""Add persistent Agentic RAG research traces.

Revision ID: 0008_agentic_research
Revises: 0007_agent_memory
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_agentic_research"
down_revision: str | None = "0007_agent_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create a queryable envelope for an atomic Research Trace graph."""

    op.create_table(
        "research_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            sa.String(length=36),
            sa.ForeignKey("learning_goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_node_id",
            sa.String(length=36),
            sa.ForeignKey("knowledge_nodes.id", ondelete="SET NULL"),
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("retrieval_mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("trace_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "retrieval_mode IN ('no_retrieval', 'single_retrieval', "
            "'multi_step_research')",
            name="research_retrieval_mode",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'insufficient_evidence', 'failed')",
            name="research_status",
        ),
    )
    op.create_index("ix_research_runs_user_id", "research_runs", ["user_id"])
    op.create_index("ix_research_runs_goal_id", "research_runs", ["goal_id"])
    op.create_index(
        "ix_research_user_created", "research_runs", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_research_goal_created", "research_runs", ["goal_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("research_runs")
