"""Relational persistence models for the first LearnLoop domain slice."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base
from app.infrastructure.database.types import UTCDateTime

UUID_LENGTH = 36


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class LearningGoalModel(Base):
    __tablename__ = "learning_goals"
    __table_args__ = (
        CheckConstraint("weekly_minutes > 0", name="weekly_minutes_positive"),
        Index("ix_learning_goals_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    desired_outcome: Mapped[str] = mapped_column(Text)
    weekly_minutes: Mapped[int] = mapped_column(Integer)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class KnowledgeNodeModel(Base):
    __tablename__ = "knowledge_nodes"
    __table_args__ = (
        CheckConstraint(
            "difficulty >= 1.0 AND difficulty <= 5.0", name="difficulty_range"
        ),
        UniqueConstraint("goal_id", "title"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    goal_id: Mapped[str] = mapped_column(
        ForeignKey("learning_goals.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class KnowledgeEdgeModel(Base):
    __tablename__ = "knowledge_edges"
    __table_args__ = (
        UniqueConstraint("source_node_id", "target_node_id", "relation"),
        CheckConstraint(
            "source_node_id <> target_node_id", name="not_self_referencing"
        ),
        Index("ix_knowledge_edges_goal_relation", "goal_id", "relation"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    goal_id: Mapped[str] = mapped_column(
        ForeignKey("learning_goals.id", ondelete="CASCADE"), index=True
    )
    source_node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE")
    )
    target_node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE")
    )
    relation: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class StudyPlanModel(Base):
    __tablename__ = "study_plans"
    __table_args__ = (
        UniqueConstraint("goal_id", "version"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    goal_id: Mapped[str] = mapped_column(
        ForeignKey("learning_goals.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class PlanItemModel(Base):
    __tablename__ = "plan_items"
    __table_args__ = (
        UniqueConstraint("plan_id", "position"),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("estimated_minutes > 0", name="estimated_minutes_positive"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("study_plans.id", ondelete="CASCADE"), index=True
    )
    knowledge_node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer)
    estimated_minutes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))


class StudySessionModel(Base):
    __tablename__ = "study_sessions"
    __table_args__ = (Index("ix_study_sessions_goal_status", "goal_id", "status"),)

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    goal_id: Mapped[str] = mapped_column(
        ForeignKey("learning_goals.id", ondelete="CASCADE"), index=True
    )
    plan_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("plan_items.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ExerciseModel(Base):
    __tablename__ = "exercises"
    __table_args__ = (CheckConstraint("max_score > 0", name="max_score_positive"),)

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    knowledge_node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), index=True
    )
    exercise_type: Mapped[str] = mapped_column(String(40))
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[list[str]] = mapped_column(JSON, default=list)
    answer_key: Mapped[list[str]] = mapped_column(JSON, default=list)
    max_score: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ExerciseAttemptModel(Base):
    __tablename__ = "exercise_attempts"
    __table_args__ = (CheckConstraint("score >= 0", name="score_non_negative"),)

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    exercise_id: Mapped[str] = mapped_column(
        ForeignKey("exercises.id", ondelete="CASCADE"), index=True
    )
    study_session_id: Mapped[str] = mapped_column(
        ForeignKey("study_sessions.id", ondelete="CASCADE"), index=True
    )
    answer: Mapped[list[str]] = mapped_column(JSON)
    score: Mapped[float] = mapped_column(Float)
    is_correct: Mapped[bool] = mapped_column(Boolean)
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime())


class MasteryEventModel(Base):
    __tablename__ = "mastery_events"
    __table_args__ = (
        CheckConstraint("delta >= -1.0 AND delta <= 1.0", name="mastery_delta_range"),
        UniqueConstraint("attempt_id", name="uq_mastery_events_attempt_id"),
        Index("ix_mastery_events_user_node", "user_id", "knowledge_node_id"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    knowledge_node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), index=True
    )
    attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("exercise_attempts.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(50))
    delta: Mapped[float] = mapped_column(Float)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())


class MasterySnapshotModel(Base):
    __tablename__ = "mastery_snapshots"
    __table_args__ = (
        CheckConstraint("score >= 0.0 AND score <= 1.0", name="mastery_score_range"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("correct_count >= 0", name="correct_count_non_negative"),
        CheckConstraint(
            "correct_count <= attempt_count", name="correct_count_within_attempts"
        ),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[float] = mapped_column(Float)
    attempt_count: Mapped[int] = mapped_column(Integer)
    correct_count: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ReviewScheduleModel(Base):
    __tablename__ = "review_schedules"
    __table_args__ = (Index("ix_review_schedules_user_due", "user_id", "due_at"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_node_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    card_json: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_review_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
