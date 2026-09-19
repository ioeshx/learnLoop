"""Durable LangGraph runtime with normalized replayable events."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.execution.models import AgentEvent, AgentRun, EngineVersion, GraphKind
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
from app.infrastructure.llm.gateway import ModelGatewayProvider
from app.observability import bind_agent_run, reset_agent_run

if TYPE_CHECKING:
    from app.agent.delegation import DelegationService
    from app.agent.dynamic.kernel import DynamicAgentKernel
    from app.agent.experience import ReflectionSkillService
    from app.agent.optimization import PolicyOptimizationService
    from app.agent.policy import AgentPolicyService
    from app.agent.research import ResearchTutor
    from app.agent.team import AgentTeamService

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
    dynamic_kernel: DynamicAgentKernel | None
    delegation: DelegationService | None = None
    experience: ReflectionSkillService | None = None
    context_debug_enabled: bool = False
    skill_admin_enabled: bool = False
    optimization: PolicyOptimizationService | None = None
    policy_admin_enabled: bool = False
    agent_policy: AgentPolicyService | None = None
    agent_policy_admin_enabled: bool = False
    team: AgentTeamService | None = None
    team_admin_enabled: bool = False
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _task_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def create_run(
        self,
        graph_kind: GraphKind,
        resource_id: str,
        *,
        engine_version: EngineVersion = "fixed_v1",
        parent_run_id: str | None = None,
    ) -> tuple[AgentRun, bool]:
        if engine_version == "dynamic_v2" and self.dynamic_kernel is None:
            raise RuntimeError("dynamic_v2 requires an enabled LLM provider")
        return await self.run_store.create_or_get(
            graph_kind,
            resource_id,
            engine_version=engine_version,
            parent_run_id=parent_run_id,
        )

    async def execute(
        self, run: AgentRun, *, resume: object = _INITIAL
    ) -> AsyncIterator[AgentEvent]:
        resumed = resume is not _INITIAL
        if run.engine_version == "dynamic_v2":
            if self.dynamic_kernel is None:
                raise RuntimeError("dynamic_v2 requires an enabled LLM provider")
            run_token = bind_agent_run(run.run_id)
            try:
                async for event in self.dynamic_kernel.execute(
                    run, resume=resume if resumed else None
                ):
                    yield event
            except asyncio.CancelledError:
                raise
            except Exception as error:
                refreshed = await self.run_store.get(run.run_id)
                if refreshed is not None and refreshed.status not in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    await self.run_store.set_status(
                        run.run_id, "failed", terminal_reason="failed"
                    )
                    yield await self.run_store.append_event(
                        run.run_id,
                        "run_failed",
                        data={
                            "terminal_reason": "failed",
                            "message": str(error),
                            "error_type": type(error).__name__,
                        },
                    )
                logger.exception("dynamic_agent_run_failed")
            finally:
                reset_agent_run(run_token)
            if self.experience is not None:
                for event in await self.experience.complete_usage(run.run_id):
                    yield event
                for event in await self.experience.process_run(run.run_id):
                    yield event
            if self.optimization is not None:
                reward = await self.optimization.evaluate_terminal_run(run.run_id)
                prior_events = await self.run_store.list_events(run.run_id)
                if reward is not None and not any(
                    item.event == "reward_recorded" for item in prior_events
                ):
                    yield await self.run_store.append_event(
                        run.run_id,
                        "reward_recorded",
                        data={
                            "reward_id": reward.id,
                            "status": reward.status,
                            "hard_gate_passed": reward.hard_gate_passed,
                            "optimization_score": reward.optimization_score,
                            "safety_violations": reward.safety_violations,
                        },
                    )
            return
        await self.run_store.set_status(run.run_id, "running")
        yield await self.run_store.append_event(
            run.run_id,
            "run_started",
            data={
                "graph": run.graph_kind,
                "engine_version": run.engine_version,
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
        # 进程内 Lock 与持久化状态校验共同阻止两个 concurrent resume 同时启动。
        async with self._task_lock:
            current = self._tasks.get(run.run_id)
            if current is not None and not current.done():
                raise RuntimeError(f"agent run {run.run_id} is already executing")
            refreshed = await self.run_store.get(run.run_id)
            if refreshed is None:
                raise LookupError(f"agent run {run.run_id} was not found")
            if resume is not _INITIAL and refreshed.status != "awaiting_input":
                raise RuntimeError("agent run is not awaiting input")
            await self.run_store.set_status(
                run.run_id, "running", expected_version=refreshed.version
            )
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
        if run.engine_version == "dynamic_v2":
            stored = await self.run_store.load_dynamic_state(run.run_id)
            return {
                "run_id": run.run_id,
                "thread_id": run.thread_id,
                "values": json.loads(stored) if stored is not None else {},
                "next": [],
                "interrupts": [],
            }
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
        # Child Runs are execution-owned projections. Delete descendants first so a
        # user cannot leave orphan Subagent traces after removing the Lead Run.
        for child in reversed(await self._descendants(run.run_id)):
            await self.checkpointer.adelete_thread(child.thread_id)
            await self.run_store.delete(child.run_id)
        await self.checkpointer.adelete_thread(run.thread_id)
        await self.run_store.delete(run.run_id)

    async def cancel_run(self, run: AgentRun) -> AgentRun:
        """持久化 cancel signal，并对 fixed_v1 执行进程内取消。

        dynamic_v2 在每轮边界 cooperative polling；fixed_v1 没有通用 Loop，因此需要
        取消当前 asyncio Task。无活动 Task（例如 awaiting_input）可立即进入 cancelled。
        """

        requested = await self.run_store.request_cancel(run.run_id)
        descendants = await self._descendants(run.run_id)
        for child in descendants:
            if child.status not in {"completed", "failed", "cancelled"}:
                await self.run_store.request_cancel(child.run_id)
        task = self._tasks.get(run.run_id)
        if run.engine_version == "fixed_v1" and task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            cancelled = await self.run_store.set_status(
                run.run_id, "cancelled", terminal_reason="cancelled"
            )
            await self.run_store.append_event(
                run.run_id, "run_cancelled", data={"terminal_reason": "cancelled"}
            )
            return cancelled
        if task is None or task.done():
            for child in descendants:
                refreshed_child = await self.run_store.get(child.run_id)
                if refreshed_child is not None and refreshed_child.status not in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    await self.run_store.set_status(
                        child.run_id, "cancelled", terminal_reason="cancelled"
                    )
                    await self.run_store.append_event(
                        child.run_id,
                        "run_cancelled",
                        data={"terminal_reason": "cancelled"},
                    )
            cancelled = await self.run_store.set_status(
                run.run_id, "cancelled", terminal_reason="cancelled"
            )
            await self.run_store.append_event(
                run.run_id, "run_cancelled", data={"terminal_reason": "cancelled"}
            )
            return cancelled
        return requested

    async def _descendants(self, run_id: str) -> list[AgentRun]:
        descendants: list[AgentRun] = []
        pending = list(await self.run_store.list_children(run_id))
        while pending:
            child = pending.pop(0)
            descendants.append(child)
            pending.extend(await self.run_store.list_children(child.run_id))
        return descendants

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
    research_tutor: ResearchTutor | None = None,
) -> AsyncGenerator[AgentRuntime, None]:
    # Local imports keep the execution package importable by dynamic submodules without
    # creating a runtime ↔ kernel circular import.
    from app.agent.delegation import DelegationService
    from app.agent.dynamic.context import ContextCompiler, token_counter_for
    from app.agent.dynamic.kernel import DynamicAgentKernel
    from app.agent.dynamic.models import RunBudget
    from app.agent.dynamic.policy import ModelAgentPolicy
    from app.agent.dynamic.tools import ToolExecutor, build_learning_tool_registry
    from app.agent.dynamic.verifier import DeterministicVerifier
    from app.agent.experience import ReflectionSkillService
    from app.agent.memory import MemoryService
    from app.agent.optimization import PolicyOptimizationService
    from app.agent.policy import AgentPolicyEngine, AgentPolicyService

    settings.ensure_runtime_directories()
    learning_tools = LearningTools(dependencies)
    async with AsyncSqliteSaver.from_conn_string(
        settings.checkpoint_path.as_posix()
    ) as checkpointer:
        await checkpointer.setup()
        run_store = await SqliteAgentRunStore.open(settings.checkpoint_path)
        memory_service = MemoryService(
            dependencies.uow_factory, clock=dependencies.clock
        )
        experience_service = ReflectionSkillService(
            run_store,
            enabled=settings.agent_skill_library_enabled,
            minimum_source_runs=settings.agent_skill_minimum_source_runs,
            recall_limit=settings.agent_skill_recall_limit,
            quarantine_min_uses=settings.agent_skill_quarantine_min_uses,
            quarantine_success_rate=settings.agent_skill_quarantine_success_rate,
        )
        optimization_service = PolicyOptimizationService(
            run_store,
            enabled=settings.agent_policy_optimization_enabled,
            expected_latency_ms=settings.agent_policy_expected_latency_ms,
        )
        await optimization_service.ensure_default_policy()
        agent_policy_service = AgentPolicyService(AgentPolicyEngine(), run_store)
        delegation_service = (
            DelegationService(
                store=run_store,
                researcher=research_tutor,
                max_children=settings.agent_max_subagents,
                max_tokens=settings.agent_delegation_max_tokens,
                max_queries=settings.agent_delegation_max_queries,
                max_sources=settings.agent_delegation_max_sources,
                deadline_seconds=settings.agent_delegation_deadline_seconds,
            )
            if research_tutor is not None
            else None
        )
        team_service = None
        if settings.agent_team_enabled and delegation_service is not None:
            from app.agent.team import (
                AgentTeamService,
                EvaluatorRoleAdapter,
                ResearcherRoleAdapter,
                RoleRegistry,
            )

            role_registry = RoleRegistry()
            role_registry.register(ResearcherRoleAdapter(delegation_service))
            role_registry.register(EvaluatorRoleAdapter())
            team_service = AgentTeamService(
                store=run_store,
                registry=role_registry,
                policy=agent_policy_service,
                default_task_tokens=settings.agent_delegation_max_tokens,
                deadline_seconds=settings.agent_team_deadline_seconds,
                max_parallel_children=(
                    settings.agent_team_max_parallel_children
                ),
                max_children=settings.agent_team_max_children,
                max_total_tokens=settings.agent_team_max_total_tokens,
            )
        runtime = AgentRuntime(
            checkpointer=checkpointer,
            run_store=run_store,
            daily_graph=build_daily_learning_graph(checkpointer),
            goal_graph=build_goal_planning_graph(checkpointer),
            tools=learning_tools,
            model=model,
            retention_days=settings.checkpoint_retention_days,
            dynamic_kernel=(
                DynamicAgentKernel(
                    store=run_store,
                    policy=ModelAgentPolicy(model),
                    tools=ToolExecutor(
                        build_learning_tool_registry(
                            learning_tools,
                            research_tutor,
                            team_service or delegation_service,
                        ),
                        policy=agent_policy_service,
                    ),
                    context=ContextCompiler(
                        artifact_reader=run_store,
                        memory_retriever=memory_service,
                        token_counter=token_counter_for(model.provider),
                        max_context_tokens=settings.agent_context_tokens,
                        reserved_output_tokens=(
                            settings.agent_context_output_reserve_tokens
                        ),
                        max_recent_observations=(
                            settings.agent_context_recent_observations
                        ),
                        source_ttl_seconds=settings.agent_context_source_ttl_seconds,
                        memory_enabled=settings.agent_memory_enabled,
                        memory_limit=settings.agent_memory_recall_limit,
                        memory_minimum_score=(
                            settings.agent_memory_minimum_score
                        ),
                        policy=agent_policy_service,
                    ),
                    verifier=DeterministicVerifier(),
                    memory=memory_service,
                    experience=experience_service,
                    optimization=optimization_service,
                    budget=RunBudget(
                        max_steps=settings.agent_max_steps,
                        max_model_calls=settings.agent_max_model_calls,
                        max_tool_calls=settings.agent_max_tool_calls,
                        max_input_tokens=settings.agent_max_input_tokens,
                        max_output_tokens=settings.agent_max_output_tokens,
                        max_total_tokens=settings.agent_max_total_tokens,
                        deadline_seconds=settings.agent_deadline_seconds,
                        max_same_action=settings.agent_max_same_action,
                        max_consecutive_failures=settings.agent_max_consecutive_failures,
                        max_replans=settings.agent_max_replans,
                    ),
                    allow_write_tools=settings.agent_dynamic_writes_enabled,
                    store_full_context=settings.agent_context_debug_full,
                )
                if model is not None
                else None
            ),
            delegation=delegation_service,
            experience=experience_service,
            context_debug_enabled=settings.agent_context_debug_full,
            skill_admin_enabled=settings.agent_skill_admin_enabled,
            optimization=optimization_service,
            policy_admin_enabled=settings.agent_policy_admin_enabled,
            agent_policy=agent_policy_service,
            agent_policy_admin_enabled=settings.agent_trust_policy_admin_enabled,
            team=team_service,
            team_admin_enabled=settings.agent_team_admin_enabled,
        )
        if model is not None:
            model.set_observer(run_store.record_model_call)
            if isinstance(model.provider, ModelGatewayProvider):
                model.provider.set_observer(run_store.save_model_route)
        await runtime.cleanup_expired()
        try:
            yield runtime
        finally:
            await runtime.shutdown()
            if model is not None:
                model.set_observer(None)
                if isinstance(model.provider, ModelGatewayProvider):
                    model.provider.set_observer(None)
            await run_store.close()


def _as_object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}
