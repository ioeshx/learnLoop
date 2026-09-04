"""Create the initial learning-domain schema.

Revision ID: 0001_initial_schema
Revises:
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_table(
        "learning_goals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("desired_outcome", sa.Text(), nullable=False),
        sa.Column("weekly_minutes", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "weekly_minutes > 0", name="ck_learning_goals_weekly_minutes_positive"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_learning_goals_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_learning_goals"),
    )
    op.create_index("ix_learning_goals_user_id", "learning_goals", ["user_id"])
    op.create_index(
        "ix_learning_goals_user_status", "learning_goals", ["user_id", "status"]
    )
    op.create_table(
        "knowledge_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("goal_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "difficulty >= 1.0 AND difficulty <= 5.0",
            name="ck_knowledge_nodes_difficulty_range",
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"], ["learning_goals.id"],
            name="fk_knowledge_nodes_goal_id_learning_goals", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_nodes"),
        sa.UniqueConstraint("goal_id", "title", name="uq_knowledge_nodes_goal_id"),
    )
    op.create_index("ix_knowledge_nodes_goal_id", "knowledge_nodes", ["goal_id"])
    op.create_table(
        "knowledge_edges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("goal_id", sa.String(length=36), nullable=False),
        sa.Column("source_node_id", sa.String(length=36), nullable=False),
        sa.Column("target_node_id", sa.String(length=36), nullable=False),
        sa.Column("relation", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "source_node_id <> target_node_id",
            name="ck_knowledge_edges_not_self_referencing",
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"], ["learning_goals.id"],
            name="fk_knowledge_edges_goal_id_learning_goals", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_node_id"], ["knowledge_nodes.id"],
            name="fk_knowledge_edges_source_node_id_knowledge_nodes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_node_id"], ["knowledge_nodes.id"],
            name="fk_knowledge_edges_target_node_id_knowledge_nodes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_edges"),
        sa.UniqueConstraint(
            "source_node_id", "target_node_id", "relation",
            name="uq_knowledge_edges_source_node_id",
        ),
    )
    op.create_index("ix_knowledge_edges_goal_id", "knowledge_edges", ["goal_id"])
    op.create_index(
        "ix_knowledge_edges_goal_relation", "knowledge_edges", ["goal_id", "relation"]
    )
    op.create_table(
        "study_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("goal_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_study_plans_version_positive"),
        sa.ForeignKeyConstraint(
            ["goal_id"], ["learning_goals.id"],
            name="fk_study_plans_goal_id_learning_goals", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_study_plans"),
        sa.UniqueConstraint("goal_id", "version", name="uq_study_plans_goal_id"),
    )
    op.create_index("ix_study_plans_goal_id", "study_plans", ["goal_id"])
    op.create_table(
        "plan_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_node_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.CheckConstraint(
            "position >= 0", name="ck_plan_items_position_non_negative"
        ),
        sa.CheckConstraint(
            "estimated_minutes > 0",
            name="ck_plan_items_estimated_minutes_positive",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_node_id"], ["knowledge_nodes.id"],
            name="fk_plan_items_knowledge_node_id_knowledge_nodes",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["study_plans.id"],
            name="fk_plan_items_plan_id_study_plans", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_plan_items"),
        sa.UniqueConstraint("plan_id", "position", name="uq_plan_items_plan_id"),
    )
    op.create_index("ix_plan_items_plan_id", "plan_items", ["plan_id"])
    op.create_table(
        "study_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("goal_id", sa.String(length=36), nullable=False),
        sa.Column("plan_item_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["goal_id"], ["learning_goals.id"],
            name="fk_study_sessions_goal_id_learning_goals", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["plan_item_id"], ["plan_items.id"],
            name="fk_study_sessions_plan_item_id_plan_items", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_study_sessions"),
    )
    op.create_index("ix_study_sessions_goal_id", "study_sessions", ["goal_id"])
    op.create_index(
        "ix_study_sessions_goal_status", "study_sessions", ["goal_id", "status"]
    )
    op.create_table(
        "exercises",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_node_id", sa.String(length=36), nullable=False),
        sa.Column("exercise_type", sa.String(length=40), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("answer_key", sa.JSON(), nullable=False),
        sa.Column("max_score", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("max_score > 0", name="ck_exercises_max_score_positive"),
        sa.ForeignKeyConstraint(
            ["knowledge_node_id"], ["knowledge_nodes.id"],
            name="fk_exercises_knowledge_node_id_knowledge_nodes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exercises"),
    )
    op.create_index(
        "ix_exercises_knowledge_node_id", "exercises", ["knowledge_node_id"]
    )
    op.create_table(
        "exercise_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("exercise_id", sa.String(length=36), nullable=False),
        sa.Column("study_session_id", sa.String(length=36), nullable=False),
        sa.Column("answer", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "score >= 0", name="ck_exercise_attempts_score_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["exercise_id"], ["exercises.id"],
            name="fk_exercise_attempts_exercise_id_exercises", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["study_session_id"], ["study_sessions.id"],
            name="fk_exercise_attempts_study_session_id_study_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exercise_attempts"),
    )
    op.create_index(
        "ix_exercise_attempts_exercise_id", "exercise_attempts", ["exercise_id"]
    )
    op.create_index(
        "ix_exercise_attempts_study_session_id",
        "exercise_attempts", ["study_session_id"]
    )
    op.create_table(
        "mastery_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_node_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("delta", sa.Float(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "delta >= -1.0 AND delta <= 1.0",
            name="ck_mastery_events_mastery_delta_range",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["exercise_attempts.id"],
            name="fk_mastery_events_attempt_id_exercise_attempts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_node_id"], ["knowledge_nodes.id"],
            name="fk_mastery_events_knowledge_node_id_knowledge_nodes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_mastery_events_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mastery_events"),
    )
    op.create_index("ix_mastery_events_user_id", "mastery_events", ["user_id"])
    op.create_index(
        "ix_mastery_events_knowledge_node_id",
        "mastery_events", ["knowledge_node_id"]
    )
    op.create_index(
        "ix_mastery_events_user_node",
        "mastery_events", ["user_id", "knowledge_node_id"]
    )
    op.create_table(
        "mastery_snapshots",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_node_id", sa.String(length=36), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("correct_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "score >= 0.0 AND score <= 1.0",
            name="ck_mastery_snapshots_mastery_score_range",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_mastery_snapshots_attempt_count_non_negative",
        ),
        sa.CheckConstraint(
            "correct_count >= 0",
            name="ck_mastery_snapshots_correct_count_non_negative",
        ),
        sa.CheckConstraint(
            "correct_count <= attempt_count",
            name="ck_mastery_snapshots_correct_count_within_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_node_id"], ["knowledge_nodes.id"],
            name="fk_mastery_snapshots_knowledge_node_id_knowledge_nodes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_mastery_snapshots_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "knowledge_node_id", name="pk_mastery_snapshots"
        ),
    )
    op.create_table(
        "review_schedules",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_node_id", sa.String(length=36), nullable=False),
        sa.Column("card_json", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("last_review_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["knowledge_node_id"], ["knowledge_nodes.id"],
            name="fk_review_schedules_knowledge_node_id_knowledge_nodes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_review_schedules_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "knowledge_node_id", name="pk_review_schedules"
        ),
    )
    op.create_index(
        "ix_review_schedules_user_due",
        "review_schedules", ["user_id", "due_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_review_schedules_user_due", table_name="review_schedules")
    op.drop_table("review_schedules")
    op.drop_table("mastery_snapshots")
    op.drop_index("ix_mastery_events_user_node", table_name="mastery_events")
    op.drop_index("ix_mastery_events_knowledge_node_id", table_name="mastery_events")
    op.drop_index("ix_mastery_events_user_id", table_name="mastery_events")
    op.drop_table("mastery_events")
    op.drop_index(
        "ix_exercise_attempts_study_session_id", table_name="exercise_attempts"
    )
    op.drop_index("ix_exercise_attempts_exercise_id", table_name="exercise_attempts")
    op.drop_table("exercise_attempts")
    op.drop_index("ix_exercises_knowledge_node_id", table_name="exercises")
    op.drop_table("exercises")
    op.drop_index("ix_study_sessions_goal_status", table_name="study_sessions")
    op.drop_index("ix_study_sessions_goal_id", table_name="study_sessions")
    op.drop_table("study_sessions")
    op.drop_index("ix_plan_items_plan_id", table_name="plan_items")
    op.drop_table("plan_items")
    op.drop_index("ix_study_plans_goal_id", table_name="study_plans")
    op.drop_table("study_plans")
    op.drop_index("ix_knowledge_edges_goal_relation", table_name="knowledge_edges")
    op.drop_index("ix_knowledge_edges_goal_id", table_name="knowledge_edges")
    op.drop_table("knowledge_edges")
    op.drop_index("ix_knowledge_nodes_goal_id", table_name="knowledge_nodes")
    op.drop_table("knowledge_nodes")
    op.drop_index("ix_learning_goals_user_status", table_name="learning_goals")
    op.drop_index("ix_learning_goals_user_id", table_name="learning_goals")
    op.drop_table("learning_goals")
    op.drop_table("users")
