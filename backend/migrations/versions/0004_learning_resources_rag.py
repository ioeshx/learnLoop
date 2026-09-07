"""Add local learning resources, chunks, vectors, and FTS5 index.

Revision ID: 0004_learning_resources_rag
Revises: 0003_lesson_content
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_learning_resources_rag"
down_revision: str | None = "0003_lesson_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_resources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("goal_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_node_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.String(length=500), nullable=True),
        sa.Column("media_type", sa.String(length=200), nullable=False),
        sa.Column("storage_key", sa.String(length=200), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("size_bytes > 0", name="ck_resource_size_positive"),
        sa.ForeignKeyConstraint(
            ["goal_id"], ["learning_goals.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_node_id"], ["knowledge_nodes.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_learning_resources_goal_status",
        "learning_resources",
        ["goal_id", "status"],
    )
    op.create_index(
        "ix_learning_resources_node_status",
        "learning_resources",
        ["knowledge_node_id", "status"],
    )
    op.create_index(
        "ix_learning_resources_sha256", "learning_resources", ["sha256"]
    )
    op.create_index(
        "ix_learning_resources_user_id", "learning_resources", ["user_id"]
    )
    op.create_index(
        "ix_learning_resources_goal_id", "learning_resources", ["goal_id"]
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_learning_resources_active_scope_hash
        ON learning_resources (
            goal_id,
            COALESCE(knowledge_node_id, ''),
            sha256
        )
        WHERE status IN ('processing', 'ready')
        """
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(length=500), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name="ck_chunk_position_non_negative"
        ),
        sa.CheckConstraint(
            "token_count > 0", name="ck_chunk_token_count_positive"
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["learning_resources.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_id", "position"),
    )
    op.create_index(
        "ix_document_chunks_resource_id", "document_chunks", ["resource_id"]
    )
    op.create_index(
        "ix_document_chunks_resource_position",
        "document_chunks",
        ["resource_id", "position"],
    )

    op.execute(
        """
        CREATE VIRTUAL TABLE document_chunks_fts USING fts5(
            content,
            section,
            content='document_chunks',
            content_rowid='rowid',
            tokenize='unicode61'
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER document_chunks_ai AFTER INSERT ON document_chunks BEGIN
            INSERT INTO document_chunks_fts(rowid, content, section)
            VALUES (new.rowid, new.content, new.section);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER document_chunks_ad AFTER DELETE ON document_chunks BEGIN
            INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content, section)
            VALUES ('delete', old.rowid, old.content, old.section);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER document_chunks_au AFTER UPDATE ON document_chunks BEGIN
            INSERT INTO document_chunks_fts(document_chunks_fts, rowid, content, section)
            VALUES ('delete', old.rowid, old.content, old.section);
            INSERT INTO document_chunks_fts(rowid, content, section)
            VALUES (new.rowid, new.content, new.section);
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS document_chunks_au")
    op.execute("DROP TRIGGER IF EXISTS document_chunks_ad")
    op.execute("DROP TRIGGER IF EXISTS document_chunks_ai")
    op.execute("DROP TABLE IF EXISTS document_chunks_fts")
    op.drop_index(
        "ix_document_chunks_resource_position", table_name="document_chunks"
    )
    op.drop_index("ix_document_chunks_resource_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.execute("DROP INDEX IF EXISTS uq_learning_resources_active_scope_hash")
    op.drop_index("ix_learning_resources_goal_id", table_name="learning_resources")
    op.drop_index("ix_learning_resources_user_id", table_name="learning_resources")
    op.drop_index("ix_learning_resources_sha256", table_name="learning_resources")
    op.drop_index(
        "ix_learning_resources_node_status", table_name="learning_resources"
    )
    op.drop_index(
        "ix_learning_resources_goal_status", table_name="learning_resources"
    )
    op.drop_table("learning_resources")
