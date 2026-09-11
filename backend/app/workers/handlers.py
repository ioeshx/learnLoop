"""First-party handlers executed by the SQLite worker."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from app.application import GetDueReviews
from app.application.errors import NotFoundError
from app.application.services import DEFAULT_USER_ID, ApplicationDependencies
from app.infrastructure.rag import RagService
from app.workers.models import BackgroundJob
from app.workers.registry import JobContext, PermanentJobError


class ResourceProcessingHandler:
    """在 Worker 中解析、切块并索引已准备好的本地学习资料。"""

    def __init__(self, rag_service: RagService) -> None:
        """复用 RAG 服务完成处理，并保持文件存储实现与 Handler 解耦。"""

        self._rag_service = rag_service

    async def __call__(
        self, job: BackgroundJob, context: JobContext
    ) -> dict[str, Any]:
        """校验任务载荷、执行资料处理，并把确定性输入错误标记为不可重试。"""

        resource_id = _required_string(job.payload, "resource_id")
        try:
            resource = await self._rag_service.process_resource(
                resource_id, report_progress=context.report_progress
            )
        except (ValueError, FileNotFoundError, LookupError, NotFoundError) as error:
            raise PermanentJobError(str(error)) from error
        return {
            "resource_id": resource.id,
            "status": resource.status.value,
            "indexed_at": resource.updated_at.isoformat(),
        }


class WeeklyReportHandler:
    """聚合指定时间窗口内的会话、练习、掌握度和到期复习统计。"""

    def __init__(
        self, database_path: Path, clock: Callable[[], datetime]
    ) -> None:
        """保存业务数据库路径和可测试时钟，报告计算不依赖 API 进程状态。"""

        self._database_path = database_path
        self._clock = clock

    async def __call__(
        self, job: BackgroundJob, context: JobContext
    ) -> dict[str, Any]:
        """用只读聚合 SQL 生成周报，并在各统计阶段持久化进度。"""

        user_id = _optional_string(job.payload, "user_id") or DEFAULT_USER_ID
        goal_id = _optional_string(job.payload, "goal_id")
        days = job.payload.get("days", 7)
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 90:
            raise PermanentJobError("weekly report days must be between 1 and 90")
        period_end = self._clock().astimezone(UTC)
        period_start = period_end - timedelta(days=days)
        await context.report_progress(20, "正在统计学习会话")
        async with aiosqlite.connect(self._database_path.as_posix()) as connection:
            connection.row_factory = aiosqlite.Row
            goal_filter = "AND s.goal_id = ?" if goal_id else ""
            session_parameters: list[object] = [
                user_id,
                period_start.isoformat(),
                period_end.isoformat(),
            ]
            if goal_id:
                session_parameters.append(goal_id)
            cursor = await connection.execute(
                f"""
                SELECT COUNT(*) AS sessions_started,
                       SUM(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END)
                           AS sessions_completed,
                       COALESCE(SUM(p.estimated_minutes), 0) AS planned_minutes
                FROM study_sessions s
                JOIN learning_goals g ON g.id = s.goal_id
                LEFT JOIN plan_items p ON p.id = s.plan_item_id
                WHERE g.user_id = ?
                  AND datetime(s.started_at) >= datetime(?)
                  AND datetime(s.started_at) <= datetime(?)
                  {goal_filter}
                """,
                session_parameters,
            )
            sessions = await cursor.fetchone()
            await cursor.close()
            await context.report_progress(50, "正在统计练习与正确率")
            attempt_parameters: list[object] = [
                user_id,
                period_start.isoformat(),
                period_end.isoformat(),
            ]
            if goal_id:
                attempt_parameters.append(goal_id)
            cursor = await connection.execute(
                f"""
                SELECT COUNT(*) AS attempts,
                       COALESCE(SUM(CASE WHEN a.is_correct = 1 THEN 1 ELSE 0 END), 0)
                           AS correct_attempts,
                       COALESCE(AVG(a.score), 0.0) AS average_score
                FROM exercise_attempts a
                JOIN study_sessions s ON s.id = a.study_session_id
                JOIN learning_goals g ON g.id = s.goal_id
                WHERE g.user_id = ?
                  AND datetime(a.attempted_at) >= datetime(?)
                  AND datetime(a.attempted_at) <= datetime(?)
                  {goal_filter}
                """,
                attempt_parameters,
            )
            attempts = await cursor.fetchone()
            await cursor.close()
            await context.report_progress(75, "正在统计掌握度与待复习项")
            cursor = await connection.execute(
                """
                SELECT COALESCE(AVG(score), 0.0) AS average_mastery
                FROM mastery_snapshots WHERE user_id = ?
                """,
                (user_id,),
            )
            mastery = await cursor.fetchone()
            await cursor.close()
            cursor = await connection.execute(
                """
                SELECT COUNT(*) AS due_reviews FROM review_schedules
                WHERE user_id = ? AND datetime(due_at) <= datetime(?)
                """,
                (user_id, period_end.isoformat()),
            )
            reviews = await cursor.fetchone()
            await cursor.close()
        if sessions is None or attempts is None or mastery is None or reviews is None:
            raise RuntimeError("weekly report query returned no aggregate row")
        attempt_count = int(attempts["attempts"])
        correct_count = int(attempts["correct_attempts"])
        return {
            "user_id": user_id,
            "goal_id": goal_id,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "sessions_started": int(sessions["sessions_started"]),
            "sessions_completed": int(sessions["sessions_completed"] or 0),
            "planned_minutes": int(sessions["planned_minutes"]),
            "attempts": attempt_count,
            "correct_attempts": correct_count,
            "accuracy": correct_count / attempt_count if attempt_count else 0.0,
            "average_score": float(attempts["average_score"]),
            "average_mastery": float(mastery["average_mastery"]),
            "due_reviews": int(reviews["due_reviews"]),
        }


class DueReviewGenerationHandler:
    """计算截止时间前的复习队列，并生成可供通知或展示的任务快照。"""

    def __init__(self, dependencies: ApplicationDependencies) -> None:
        """注入应用依赖，使后台计算与同步到期复习接口复用同一业务规则。"""

        self._dependencies = dependencies

    async def __call__(
        self, job: BackgroundJob, context: JobContext
    ) -> dict[str, Any]:
        """解析带时区的截止时间，查询复习项并逐项报告生成进度。"""

        due_before_value = _required_string(job.payload, "due_before")
        try:
            due_before = datetime.fromisoformat(due_before_value)
        except ValueError as error:
            raise PermanentJobError("due_before must be an ISO datetime") from error
        if due_before.tzinfo is None:
            raise PermanentJobError("due_before must include a timezone")
        await context.report_progress(30, "正在查询到期复习项")
        reviews = await GetDueReviews(self._dependencies).execute(
            due_before=due_before
        )
        tasks: list[dict[str, Any]] = []
        total = len(reviews)
        for index, review in enumerate(reviews, start=1):
            tasks.append(
                {
                    "knowledge_node_id": review.knowledge_node.id,
                    "knowledge_node_title": review.knowledge_node.title,
                    "due_at": review.schedule.due_at.isoformat(),
                    "exercise_id": review.exercise.id if review.exercise else None,
                }
            )
            await context.report_progress(
                30 + int(60 * index / max(total, 1)),
                f"已生成 {index}/{total} 个复习任务",
            )
        return {
            "user_id": _optional_string(job.payload, "user_id")
            or DEFAULT_USER_ID,
            "due_before": due_before.isoformat(),
            "count": total,
            "tasks": tasks,
        }


def _required_string(payload: dict[str, Any], key: str) -> str:
    """读取并规范化必填字符串载荷，格式错误时禁止无意义重试。"""

    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PermanentJobError(f"job payload requires non-empty {key}")
    return value.strip()


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    """读取可选字符串载荷；提供了空值或错误类型时判定任务不可执行。"""

    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PermanentJobError(f"job payload {key} must be a non-empty string")
    return value.strip()
