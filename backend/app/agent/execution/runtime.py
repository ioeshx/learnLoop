"""Durable LangGraph runtime with normalized replayable events."""

import asyncio
import logging
from collections.abc import AsyncIterator, AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.execution.models import AgentEvent, AgentRun, GraphKind
from app.agent.execution.store import SqliteAgentRunStore
from app.agent.graphs import (
    DailyLearningContext,
    GoalPlanningContext,
    build_daily_learning_graph,
    build_goal_planning_graph,
)
from app.agent.states import GoalPlanningState, StudySessionState
from app.agent.tools import LearningTools
from app.application import ApplicationDependencies
from app.config import Settings
from app.infrastructure.llm import StructuredModel
from app.observability import bind_agent_run, reset_agent_run

_INITIAL = object()
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentRuntime:
    checkpointer: AsyncSqliteSaver
    run_store: SqliteAgentRunStore
    daily_graph: CompiledStateGraph[
        StudySessionState,
        DailyLearningContext,
        StudySessionState,
        StudySessionState,
    ]
    goal_graph: CompiledStateGraph[
        GoalPlanningState,
        GoalPlanningContext,
        GoalPlanningState,
        GoalPlanningState,
    ]
    tools: LearningTools
    model: StructuredModel | None
    retention_days: int
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)

    async def create_run(
        self, graph_kind: GraphKind, resource_id: str
    ) -> tuple[AgentRun, bool]:
        return await self.run_store.create_or_get(graph_kind, resource_id)

    async def execute(
        self, run: AgentRun, *, resume: object = _INITIAL
    ) -> AsyncIterator[AgentEvent]:
        resumed = resume is not _INITIAL
        await self.run_store.set_status(run.run_id, "running")
        yield await self.run_store.append_event(
            run.run_id,
            "run_started",
            data={
                "graph": run.graph_kind,
                "thread_id": run.thread_id,
                "resumed": resumed,
            },
        )
        run_token = bind_agent_run(run.run_id)
        started_at = datetime.now(UTC)
        logger.info(
            "agent_run_started",
            extra={
                "run_id": run.run_id,
                "graph": run.graph_kind,
                "status": "running",
            },
        )
        try:
            graph, context = self._graph_and_context(run.graph_kind)
            graph_input: object
            if resumed:
                graph_input = Command(resume=resume)
            else:
                graph_input = {
                    "run_id": run.run_id,
                    (
                        "session_id"
                        if run.graph_kind == "daily_learning"
                        else "goal_id"
                    ): run.resource_id,
                }
            config: RunnableConfig = {
                "configurable": {"thread_id": run.thread_id}
            }
            interrupted = False
            stream = graph.astream(
                graph_input,
                config=config,
                context=context,
                stream_mode=["tasks", "custom"],
                version="v2",
            )
            async for raw_chunk in cast(AsyncIterator[object], stream):
                chunk = _as_object_dict(raw_chunk)
                chunk_type = chunk.get("type")
                data = _as_object_dict(chunk.get("data"))
                if chunk_type == "tasks":
                    async for event in self._task_events(run.run_id, data):
                        if event.event == "interrupt_created":
                            interrupted = True
                        yield event
                elif chunk_type == "custom":
                    custom_event = await self._custom_event(run.run_id, data)
                    if custom_event is not None:
                        yield custom_event

            if interrupted:
                await self.run_store.set_status(run.run_id, "awaiting_input")
                logger.info(
                    "agent_run_awaiting_input",
                    extra={
                        "run_id": run.run_id,
                        "graph": run.graph_kind,
                        "status": "awaiting_input",
                        "duration_ms": round(
                            (datetime.now(UTC) - started_at).total_seconds()
                            * 1000,
                            3,
                        ),
                    },
                )
                return
            snapshot = await graph.aget_state(config)
            values = _as_object_dict(snapshot.values)
            await self.run_store.set_status(run.run_id, "completed")
            yield await self.run_store.append_event(
                run.run_id,
                "run_completed",
                data={
                    "status": values.get("status", "completed"),
                    "plan_id": values.get("plan_id"),
                    "summary": values.get("summary"),
                },
            )
            logger.info(
                "agent_run_completed",
                extra={
                    "run_id": run.run_id,
                    "graph": run.graph_kind,
                    "status": "completed",
                    "duration_ms": round(
                        (datetime.now(UTC) - started_at).total_seconds() * 1000,
                        3,
                    ),
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.run_store.set_status(run.run_id, "failed")
            yield await self.run_store.append_event(
                run.run_id,
                "run_failed",
                data={"message": str(error), "error_type": type(error).__name__},
            )
            logger.exception(
                "agent_run_failed",
                extra={
                    "run_id": run.run_id,
                    "graph": run.graph_kind,
                    "status": "failed",
                    "duration_ms": round(
                        (datetime.now(UTC) - started_at).total_seconds() * 1000,
                        3,
                    ),
                },
            )
        finally:
            reset_agent_run(run_token)

    async def start_execution(
        self, run: AgentRun, *, resume: object = _INITIAL
    ) -> None:
        current = self._tasks.get(run.run_id)
        if current is not None and not current.done():
            raise RuntimeError(f"agent run {run.run_id} is already executing")
        await self.run_store.set_status(run.run_id, "running")
        task = asyncio.create_task(
            self._consume_execution(run, resume=resume),
            name=f"learnloop-agent-{run.run_id}",
        )
        self._tasks[run.run_id] = task
        task.add_done_callback(self._discard_task)

    async def _consume_execution(
        self, run: AgentRun, *, resume: object
    ) -> None:
        async for _ in self.execute(run, resume=resume):
            pass

    def _discard_task(self, completed: asyncio.Task[None]) -> None:
        if not completed.cancelled():
            completed.exception()
        for run_id, task in tuple(self._tasks.items()):
            if task is completed:
                self._tasks.pop(run_id, None)
                return

    async def _task_events(
        self, run_id: str, data: dict[str, object]
    ) -> AsyncIterator[AgentEvent]:
        node = data.get("name")
        node_name = node if isinstance(node, str) else None
        is_completion = any(
            key in data for key in ("result", "error", "interrupts")
        )
        if not is_completion:
            yield await self.run_store.append_event(
                run_id, "node_started", node=node_name
            )
            return
        interrupts = data.get("interrupts", [])
        if isinstance(interrupts, (list, tuple)) and interrupts:
            for item in interrupts:
                payload = _as_object_dict(item)
                value = payload.get("value")
                yield await self.run_store.append_event(
                    run_id,
                    "interrupt_created",
                    node=node_name,
                    data={
                        "interrupt_id": payload.get("id"),
                        "value": value,
                    },
                )
            return
        if data.get("error") is None:
            result = data.get("result")
            result_dict = _as_object_dict(result)
            yield await self.run_store.append_event(
                run_id,
                "node_completed",
                node=node_name,
                data={"updated_fields": sorted(result_dict)},
            )

    async def _custom_event(
        self, run_id: str, data: dict[str, object]
    ) -> AgentEvent | None:
        event = data.get("event")
        if event not in {"tool_started", "tool_completed"}:
            return None
        node = data.get("tool")
        details = data.get("data", {})
        tool_name = node if isinstance(node, str) else "unknown"
        payload = _as_object_dict(details)
        call_id = payload.get("call_id")
        if isinstance(call_id, str):
            if event == "tool_started":
                started_at = payload.get("started_at")
                await self.run_store.start_tool_call(
                    call_id=call_id,
                    run_id=run_id,
                    tool_name=tool_name,
                    arguments=_as_object_dict(payload.get("arguments")),
                    started_at=(
                        datetime.fromisoformat(started_at)
                        if isinstance(started_at, str)
                        else datetime.now(UTC)
                    ),
                )
            else:
                completed_at = payload.get("completed_at")
                duration_ms = payload.get("duration_ms", 0.0)
                await self.run_store.finish_tool_call(
                    call_id=call_id,
                    result_summary=_as_object_dict(payload.get("result_summary")),
                    status=str(payload.get("status", "succeeded")),
                    duration_ms=(
                        float(duration_ms)
                        if isinstance(duration_ms, (int, float))
                        else 0.0
                    ),
                    error=(
                        str(payload["error"])
                        if payload.get("error") is not None
                        else None
                    ),
                    completed_at=(
                        datetime.fromisoformat(completed_at)
                        if isinstance(completed_at, str)
                        else datetime.now(UTC)
                    ),
                )
        return await self.run_store.append_event(
            run_id,
            event,
            node=node if isinstance(node, str) else None,
            data=payload,
        )

    def _graph_and_context(
        self, graph_kind: GraphKind
    ) -> tuple[Any, DailyLearningContext | GoalPlanningContext]:
        if graph_kind == "daily_learning":
            return self.daily_graph, DailyLearningContext(
                tools=self.tools,
                model=self.model,
                review_grades=True,
            )
        if self.model is None:
            raise RuntimeError("goal planning requires an enabled LLM provider")
        return self.goal_graph, GoalPlanningContext(
            tools=self.tools, model=self.model
        )

    async def cleanup_expired(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        candidates = await self.run_store.cleanup_candidates(cutoff)
        for run in candidates:
            await self.checkpointer.adelete_thread(run.thread_id)
            await self.run_store.delete(run.run_id)
        return len(candidates)

    async def get_checkpoint_state(self, run: AgentRun) -> dict[str, object]:
        graph = (
            self.daily_graph
            if run.graph_kind == "daily_learning"
            else self.goal_graph
        )
        config: RunnableConfig = {
            "configurable": {"thread_id": run.thread_id}
        }
        snapshot = await graph.aget_state(config)
        return {
            "run_id": run.run_id,
            "thread_id": run.thread_id,
            "values": _as_object_dict(snapshot.values),
            "next": list(snapshot.next),
            "interrupts": [
                {"id": item.id, "value": item.value}
                for item in snapshot.interrupts
            ],
        }

    async def delete_run(self, run: AgentRun) -> None:
        task = self._tasks.get(run.run_id)
        if task is not None and not task.done():
            raise RuntimeError("cannot delete an Agent run while it is executing")
        await self.checkpointer.adelete_thread(run.thread_id)
        await self.run_store.delete(run.run_id)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


@asynccontextmanager
async def open_agent_runtime(
    settings: Settings,
    dependencies: ApplicationDependencies,
    model: StructuredModel | None,
) -> AsyncGenerator[AgentRuntime, None]:
    settings.ensure_runtime_directories()
    async with AsyncSqliteSaver.from_conn_string(
        settings.checkpoint_path.as_posix()
    ) as checkpointer:
        await checkpointer.setup()
        run_store = await SqliteAgentRunStore.open(settings.checkpoint_path)
        runtime = AgentRuntime(
            checkpointer=checkpointer,
            run_store=run_store,
            daily_graph=build_daily_learning_graph(checkpointer),
            goal_graph=build_goal_planning_graph(checkpointer),
            tools=LearningTools(dependencies),
            model=model,
            retention_days=settings.checkpoint_retention_days,
        )
        if model is not None:
            model.set_observer(run_store.record_model_call)
        await runtime.cleanup_expired()
        try:
            yield runtime
        finally:
            await runtime.shutdown()
            if model is not None:
                model.set_observer(None)
            await run_store.close()


def _as_object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}
