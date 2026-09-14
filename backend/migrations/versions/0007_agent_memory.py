"""Add governed Agent Memory aggregates.

Revision ID: 0007_agent_memory
Revises: 0006_adaptive_review_sessions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_agent_memory"
down_revision: str | None = "0006_adaptive_review_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create Memory state, immutable Evidence, and Revision history tables."""

    op.create_table(
        "memory_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("memory_key", sa.String(length=300), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("trust", sa.String(length=30), nullable=False),
        sa.Column("sensitivity", sa.String(length=30), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column(
            "goal_id",
            sa.String(length=36),
            sa.ForeignKey("learning_goals.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "knowledge_node_id",
            sa.String(length=36),
            sa.ForeignKey("knowledge_nodes.id", ondelete="CASCADE"),
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "supersedes_id",
            sa.String(length=36),
            sa.ForeignKey("memory_records.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('working', 'episodic', 'semantic', 'procedural')",
            name="memory_kind",
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'active', 'rejected', 'expired')",
            name="memory_status",
        ),
        sa.CheckConstraint(
            "trust IN ('untrusted', 'user_asserted', 'verified', 'system')",
            name="memory_trust",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('normal', 'personal', 'sensitive')",
            name="memory_sensitivity",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="memory_confidence_range"
        ),
        sa.CheckConstraint(
            "importance >= 0 AND importance <= 1", name="memory_importance_range"
        ),
    )
    op.create_index("ix_memory_records_user_id", "memory_records", ["user_id"])
    op.create_index(
        "ix_memory_user_status_kind", "memory_records", ["user_id", "status", "kind"]
    )
    op.create_index(
        "ix_memory_user_key_status",
        "memory_records",
        ["user_id", "memory_key", "status"],
    )
    op.create_index(
        "ix_memory_goal_node_status",
        "memory_records",
        ["goal_id", "knowledge_node_id", "status"],
    )
    op.create_index(
        "ix_memory_user_fingerprint",
        "memory_records",
        ["user_id", "fingerprint"],
    )
    op.create_table(
        "memory_evidence",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "memory_id",
            sa.String(length=36),
            sa.ForeignKey("memory_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("trust", sa.String(length=30), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.String(length=36)),
        sa.Column("session_id", sa.String(length=36)),
        sa.Column("attempt_id", sa.String(length=36)),
    )
    op.create_index(
        "ix_memory_evidence_memory_id", "memory_evidence", ["memory_id"]
    )
    op.create_index(
        "ix_memory_evidence_memory_observed",
        "memory_evidence",
        ["memory_id", "observed_at"],
    )
    op.create_index(
        "ix_memory_evidence_source",
        "memory_evidence",
        ["source_type", "source_id"],
    )
    op.create_table(
        "memory_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "memory_id",
            sa.String(length=36),
            sa.ForeignKey("memory_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("previous_content", sa.Text()),
        sa.Column("new_content", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("memory_id", "revision"),
        sa.CheckConstraint("revision > 0", name="memory_revision_positive"),
    )
    op.create_index(
        "ix_memory_revisions_memory_id", "memory_revisions", ["memory_id"]
    )


def downgrade() -> None:
    op.drop_table("memory_revisions")
    op.drop_table("memory_evidence")
    op.drop_table("memory_records")
