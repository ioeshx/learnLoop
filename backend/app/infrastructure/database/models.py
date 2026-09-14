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
    text,
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
    lesson_content: Mapped[str] = mapped_column(Text, default="", server_default="")
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
    kind: Mapped[str] = mapped_column(String(30), default="learning")
    exercise_id: Mapped[str | None] = mapped_column(
        ForeignKey("exercises.id", ondelete="SET NULL"), nullable=True
    )
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


class LearningResourceModel(Base):
    __tablename__ = "learning_resources"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="resource_size_positive"),
        Index("ix_learning_resources_goal_status", "goal_id", "status"),
        Index("ix_learning_resources_node_status", "knowledge_node_id", "status"),
        Index("ix_learning_resources_sha256", "sha256"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    goal_id: Mapped[str] = mapped_column(
        ForeignKey("learning_goals.id", ondelete="CASCADE"), index=True
    )
    knowledge_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(20))
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_type: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class DocumentChunkModel(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("resource_id", "position"),
        CheckConstraint("position >= 0", name="chunk_position_non_negative"),
        CheckConstraint("token_count > 0", name="chunk_token_count_positive"),
        Index("ix_document_chunks_resource_position", "resource_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("learning_resources.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(500), nullable=True)
    embedding: Mapped[list[float]] = mapped_column(JSON)
    embedding_model: Mapped[str] = mapped_column(String(200))


class BackgroundJobModel(Base):
    """后台任务的 SQLAlchemy 表映射。

    该持久化模型保存任务载荷、结果、进度、重试状态和 Worker 租约；检查约束保护状态机
    基本不变量，领取/租约索引支持轮询，部分唯一索引实现同类型任务的幂等入队。
    """

    __tablename__ = "background_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="background_jobs_status",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="background_jobs_progress",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name="background_jobs_attempts",
        ),
        Index("ix_background_jobs_claim", "status", "available_at", "created_at"),
        Index("ix_background_jobs_lease", "status", "lease_expires_at"),
        Index("ix_background_jobs_type_created", "job_type", "created_at"),
        Index(
            "uq_background_jobs_type_idempotency",
            "job_type",
            "idempotency_key",
            unique=True,
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(100))
    payload_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    progress: Mapped[int] = mapped_column(Integer)
    progress_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime())
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    cancel_requested: Mapped[bool] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class MemoryRecordModel(Base):
    """Queryable materialized state for a governed long-term Memory item."""

    __tablename__ = "memory_records"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('working', 'episodic', 'semantic', 'procedural')",
            name="memory_kind",
        ),
        CheckConstraint(
            "status IN ('candidate', 'active', 'rejected', 'expired')",
            name="memory_status",
        ),
        CheckConstraint(
            "trust IN ('untrusted', 'user_asserted', 'verified', 'system')",
            name="memory_trust",
        ),
        CheckConstraint(
            "sensitivity IN ('normal', 'personal', 'sensitive')",
            name="memory_sensitivity",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="memory_confidence_range"
        ),
        CheckConstraint(
            "importance >= 0 AND importance <= 1", name="memory_importance_range"
        ),
        Index("ix_memory_user_status_kind", "user_id", "status", "kind"),
        Index("ix_memory_user_key_status", "user_id", "memory_key", "status"),
        Index("ix_memory_goal_node_status", "goal_id", "knowledge_node_id", "status"),
        Index("ix_memory_user_fingerprint", "user_id", "fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    attributes: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    memory_key: Mapped[str] = mapped_column(String(300))
    fingerprint: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    importance: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30))
    trust: Mapped[str] = mapped_column(String(30))
    sensitivity: Mapped[str] = mapped_column(String(30))
    requires_approval: Mapped[bool] = mapped_column(Boolean)
    goal_id: Mapped[str | None] = mapped_column(
        ForeignKey("learning_goals.id", ondelete="CASCADE"), nullable=True
    )
    knowledge_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), nullable=True
    )
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_records.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class MemoryEvidenceModel(Base):
    __tablename__ = "memory_evidence"
    __table_args__ = (
        Index("ix_memory_evidence_memory_observed", "memory_id", "observed_at"),
        Index("ix_memory_evidence_source", "source_type", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("memory_records.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(80))
    source_id: Mapped[str] = mapped_column(String(200))
    excerpt: Mapped[str] = mapped_column(Text)
    trust: Mapped[str] = mapped_column(String(30))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    run_id: Mapped[str | None] = mapped_column(String(UUID_LENGTH), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(UUID_LENGTH), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(UUID_LENGTH), nullable=True)


class MemoryRevisionModel(Base):
    __tablename__ = "memory_revisions"
    __table_args__ = (
        UniqueConstraint("memory_id", "revision"),
        CheckConstraint("revision > 0", name="memory_revision_positive"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("memory_records.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    previous_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_content: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(500))
    actor: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ResearchRunModel(Base):
    """Relational envelope around an atomic Agentic RAG Trace graph."""

    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint(
            "retrieval_mode IN ('no_retrieval', 'single_retrieval', "
            "'multi_step_research')",
            name="research_retrieval_mode",
        ),
        CheckConstraint(
            "status IN ('completed', 'insufficient_evidence', 'failed')",
            name="research_status",
        ),
        Index("ix_research_user_created", "user_id", "created_at"),
        Index("ix_research_goal_created", "goal_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    goal_id: Mapped[str] = mapped_column(
        ForeignKey("learning_goals.id", ondelete="CASCADE"), index=True
    )
    knowledge_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text)
    retrieval_mode: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    trace_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime] = mapped_column(UTCDateTime())
