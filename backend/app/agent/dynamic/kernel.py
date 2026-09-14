"""Bounded observe-decide-act-verify loop for the LearnLoop v2 Agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

from app.agent.dynamic.budget import BudgetLedger
from app.agent.dynamic.context import ContextCompiler, ContextPackage, ContextPurpose
from app.agent.dynamic.models import (
    ActionType,
    AgentAction,
    AgentInterrupt,
    AgentPlan,
    BudgetUsage,
    DynamicAgentState,
    Observation,
    PlanStep,
    RunBudget,
    StepStatus,
    ToolError,
    ToolErrorKind,
    ToolResult,
    ToolSpec,
    VerificationStatus,
)
from app.agent.dynamic.policy import AgentPolicy
from app.agent.dynamic.tools import ToolExecutor
from app.agent.dynamic.verifier import DeterministicVerifier, apply_replan
from app.agent.execution.models import AgentEvent, AgentRun
from app.agent.execution.store import SqliteAgentRunStore
from app.agent.memory import MemoryService
from app.application.services import DEFAULT_USER_ID


class DynamicAgentKernel:
    """运行受控的 ``observe → decide → act → verify → replan`` state machine。

    这个类刻意不使用自由形式的 autonomous loop。每轮最多执行一个公开 Action，所有
    Side Effect 必须通过 ToolExecutor，Step 完成必须通过 Verifier，循环继续之前会持久化
    durable state。由此可以在 crash/restart 后 resume，也可以完整回放每个公开决策。
    """

    def __init__(
        self,
        *,
        store: SqliteAgentRunStore,
        policy: AgentPolicy,
        tools: ToolExecutor,
        context: ContextCompiler,
        verifier: DeterministicVerifier,
        budget: RunBudget,
        memory: MemoryService | None = None,
        allow_write_tools: bool = False,
        store_full_context: bool = False,
    ) -> None:
        self.store = store
        self.policy = policy
        self.tools = tools
        self.context = context
        self.verifier = verifier
        self.default_budget = budget
        self.memory = memory
        self.allow_write_tools = allow_write_tools
        self.store_full_context = store_full_context

    async def execute(
        self, run: AgentRun, *, resume: object | None = None
    ) -> AsyncIterator[AgentEvent]:
        if run.graph_kind != "daily_learning":
            raise ValueError("dynamic_v2 currently supports daily_learning only")
        await self.store.set_status(run.run_id, "running")
        yield await self.store.append_event(
            run.run_id,
            "run_started",
            data={
                "graph": run.graph_kind,
                "engine_version": run.engine_version,
                "attempt_no": run.attempt_no,
                "resumed": resume is not None,
            },
        )

        state = await self._load_or_initialize(run)
        if state is None:
            refreshed = await self.store.get(run.run_id)
            if refreshed is not None and refreshed.status == "failed":
                return
            raise RuntimeError("dynamic Agent initialization failed")
        if resume is not None:
            state, resume_event = await self._apply_resume(state, resume)
            await self._save(state)
            yield await self.store.append_event(
                run.run_id,
                "observation_recorded",
                node=resume_event.plan_step_id,
                data=_observation_trace(resume_event),
            )

        while True:
            refreshed = await self.store.get(run.run_id)
            if refreshed is None:
                raise LookupError(f"agent run {run.run_id} was not found")
            if refreshed.cancel_requested:
                await self._save(state)
                yield await self._terminate(run.run_id, "cancelled")
                return

            ledger = BudgetLedger(state.budget, state.usage)
            budget_check = ledger.preflight("model")
            if not budget_check.allowed:
                await self._save(state)
                yield await self._terminate(
                    run.run_id,
                    budget_check.terminal_reason or "budget_exhausted",
                    budget_check.detail,
                )
                return

            step = state.plan.ready_step()
            if step is None:
                if state.plan.complete:
                    memory_event = await self._capture_completed_memory(state)
                    if memory_event is not None:
                        yield memory_event
                    yield await self._terminate(
                        run.run_id, "completed", state.final_summary
                    )
                else:
                    yield await self._terminate(
                        run.run_id,
                        "verification_failed",
                        "Plan has no ready Step but is not complete",
                    )
                return

            state = state.model_copy(
                update={
                    "plan": _activate_step(state.plan, step.id),
                    "usage": ledger.record_step(),
                }
            )
            step = _step_by_id(state.plan, step.id)
            tool_specs = self._allowed_specs(step)
            context, context_event = await self._compile_context(
                state, step, tool_specs, purpose=ContextPurpose.DECISION
            )
            yield context_event
            decision = await self.policy.decide(context)
            await self.store.record_context_token_observation(
                context.snapshot.snapshot_id, decision.usage.input_tokens
            )
            token_decision = ledger.record_model(decision.usage)
            state = state.model_copy(update={"usage": ledger.usage})
            if not token_decision.allowed:
                await self._save(state)
                yield await self._budget_event(state)
                yield await self._terminate(
                    run.run_id,
                    token_decision.terminal_reason or "budget_exhausted",
                    token_decision.detail,
                )
                return

            action = decision.value
            action_id = str(uuid4())
            rejection = self._validate_action(action, step)
            if rejection is not None:
                state = _record_failure(state)
                await self._save(state)
                yield await self.store.append_event(
                    run.run_id,
                    "action_rejected",
                    node=step.id,
                    data={"action_id": action_id, "reason": rejection},
                )
                if state.consecutive_failures >= state.budget.max_consecutive_failures:
                    yield await self._terminate(
                        run.run_id, "verification_failed", rejection
                    )
                    return
                continue

            state, repeated = _record_action_signature(state, action)
            yield await self.store.append_event(
                run.run_id,
                "action_decided",
                node=step.id,
                data={"action_id": action_id, **_action_trace(action)},
            )
            if repeated > state.budget.max_same_action:
                state = _record_failure(state)
                await self._save(state)
                yield await self.store.append_event(
                    run.run_id,
                    "action_rejected",
                    node=step.id,
                    data={
                        "action_id": action_id,
                        "reason": "equivalent action repetition limit exceeded",
                    },
                )
                yield await self._terminate(
                    run.run_id,
                    "verification_failed",
                    "equivalent action repetition limit exceeded",
                )
                return

            if action.action == ActionType.CALL_TOOL:
                before_tool = await self.store.get(run.run_id)
                if before_tool is not None and before_tool.cancel_requested:
                    await self._save(state)
                    yield await self._terminate(run.run_id, "cancelled")
                    return
                async for event in self._execute_tool_action(
                    run, state, step, action, action_id
                ):
                    yield event
                loaded = await self.store.load_dynamic_state(run.run_id)
                if loaded is None:
                    raise RuntimeError("dynamic state disappeared after Tool execution")
                state = DynamicAgentState.model_validate_json(loaded)
                current_run = await self.store.get(run.run_id)
                if current_run is not None and current_run.status in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    return
                if current_run is not None and current_run.cancel_requested:
                    await self._save(state)
                    yield await self._terminate(run.run_id, "cancelled")
                    return
                latest = state.observations[-1]
                should_replan = (
                    not latest.succeeded
                    and latest.error_kind
                    not in {ToolErrorKind.TRANSIENT, ToolErrorKind.TIMEOUT}
                ) or (
                    state.consecutive_failures
                    >= state.budget.max_consecutive_failures
                )
                if should_replan:
                    replanned = await self._try_replan(run, state)
                    if replanned is None:
                        yield await self._terminate(
                            run.run_id,
                            "verification_failed",
                            "Tool failures exhausted the Replanner",
                        )
                        return
                    state, context_event, replan_event, budget_event = replanned
                    yield context_event
                    yield budget_event
                    yield replan_event
                continue

            if action.action == ActionType.PRESENT_CONTENT:
                observation = Observation(
                    action_id=action_id,
                    plan_step_id=step.id,
                    source="agent.content",
                    succeeded=True,
                    summary="Content was presented to the learner.",
                    data=await self._artifact_data(
                        run.run_id, "agent_content", action.content
                    ),
                )
                state = _append_observation(state, observation, succeeded=True)
                await self._save(state)
                yield await self.store.append_event(
                    run.run_id,
                    "content_presented",
                    node=step.id,
                    data={
                        "action_id": action_id,
                        "observation_id": observation.id,
                        "content": action.content,
                    },
                )
                continue

            if action.action == ActionType.REQUEST_INPUT:
                interrupt = AgentInterrupt(
                    type=action.input_type or "clarification",
                    prompt=action.content or "请提供继续所需的信息。",
                    plan_step_id=step.id,
                    allowed_actions=(
                        ["accept", "reject"]
                        if action.input_type == "approval"
                        else []
                    ),
                )
                state = state.model_copy(update={"pending_interrupt": interrupt})
                await self._save(state)
                await self.store.set_status(run.run_id, "awaiting_input")
                yield await self.store.append_event(
                    run.run_id,
                    "interrupt_created",
                    node=step.id,
                    data={
                        "interrupt_id": interrupt.id,
                        "value": interrupt.model_dump(mode="json"),
                    },
                )
                yield await self.store.append_event(
                    run.run_id,
                    "run_paused",
                    node=step.id,
                    data={"reason": interrupt.type},
                )
                return

            if action.action == ActionType.COMPLETE_STEP:
                verification = self.verifier.verify_step(
                    step, action.evidence_ids, state.observations
                )
                yield await self.store.append_event(
                    run.run_id,
                    "verification_completed",
                    node=step.id,
                    data=verification.model_dump(mode="json"),
                )
                if verification.status == VerificationStatus.PASSED:
                    state = state.model_copy(
                        update={
                            "plan": _complete_step(
                                state.plan, step.id, verification.evidence_ids
                            ),
                            "consecutive_failures": 0,
                        }
                    )
                else:
                    state = _record_failure(state)
                await self._save(state)
                continue

            if action.action == ActionType.FINISH_RUN:
                final = self.verifier.verify_plan(state.plan)
                yield await self.store.append_event(
                    run.run_id,
                    "verification_completed",
                    data=final.model_dump(mode="json"),
                )
                if final.status == VerificationStatus.PASSED:
                    state = state.model_copy(update={"final_summary": action.content})
                    await self._save(state)
                    memory_event = await self._capture_completed_memory(state)
                    if memory_event is not None:
                        yield memory_event
                    yield await self._terminate(run.run_id, "completed", action.content)
                    return
                state = _record_failure(state)
                await self._save(state)

    async def _load_or_initialize(self, run: AgentRun) -> DynamicAgentState | None:
        stored = await self.store.load_dynamic_state(run.run_id)
        if stored is not None:
            return DynamicAgentState.model_validate_json(stored)

        usage = BudgetUsage()
        ledger = BudgetLedger(self.default_budget, usage)
        bootstrap_call_id = str(uuid4())
        bootstrap_started = datetime.now(UTC)
        await self.store.start_tool_call(
            call_id=bootstrap_call_id,
            run_id=run.run_id,
            tool_name="session.get_state",
            arguments={"session_id": run.resource_id},
            started_at=bootstrap_started,
        )
        await self.store.append_event(
            run.run_id,
            "tool_started",
            node="session.get_state",
            data={"call_id": bootstrap_call_id, "plan_step_id": "bootstrap"},
        )
        bootstrap = await self.tools.execute(
            name="session.get_state",
            arguments={"session_id": run.resource_id},
            allowed_tools={"session.get_state"},
            run_id=run.run_id,
            plan_step_id="bootstrap",
        )
        ledger.record_tool()
        await self.store.finish_tool_call(
            call_id=bootstrap_call_id,
            result_summary=_tool_result_summary(bootstrap),
            status="succeeded" if bootstrap.succeeded else "failed",
            duration_ms=bootstrap.duration_ms,
            error=bootstrap.error.message if bootstrap.error else None,
            completed_at=datetime.now(UTC),
        )
        await self.store.append_event(
            run.run_id,
            "tool_completed",
            node="session.get_state",
            data={"call_id": bootstrap_call_id, **_tool_result_summary(bootstrap)},
        )
        if not bootstrap.succeeded or not isinstance(bootstrap.output, dict):
            await self.store.set_status(run.run_id, "failed", terminal_reason="failed")
            await self.store.append_event(
                run.run_id,
                "run_failed",
                data={
                    "reason": "bootstrap_failed",
                    "tool": bootstrap.model_dump(mode="json"),
                },
            )
            return None
        title = bootstrap.output.get("knowledge_node_title", run.resource_id)
        objective = f"完成学习 Session：{title}"
        tools = self._all_specs()
        planner_request = self.context.initial_request(
            run.run_id,
            objective,
            tools,
            self.default_budget,
            ledger.usage,
        )
        planner_context = self.context.compile_initial(
            planner_request,
            bootstrap.output,
            tools,
            max_plan_steps=min(6, self.default_budget.max_steps),
        )
        await self._persist_context(planner_context, node="planner")
        try:
            planned = await self.policy.create_plan(
                context=planner_context,
            )
            await self.store.record_context_token_observation(
                planner_context.snapshot.snapshot_id,
                planned.usage.input_tokens,
            )
        except Exception as error:
            await self.store.append_event(
                run.run_id,
                "plan_rejected",
                data={
                    "reason": "planner_output_invalid",
                    "error": f"{type(error).__name__}: {str(error)[:1_000]}",
                },
            )
            await self.store.set_status(
                run.run_id, "failed", terminal_reason="failed"
            )
            return None
        token_decision = ledger.record_model(planned.usage)
        if not token_decision.allowed:
            await self.store.set_status(
                run.run_id,
                "failed",
                terminal_reason=token_decision.terminal_reason or "budget_exhausted",  # type: ignore[arg-type]
            )
            return None
        try:
            plan = _normalize_and_validate_plan(
                planned.value, {item.name for item in tools}
            )
        except ValueError as error:
            await self.store.append_event(
                run.run_id,
                "plan_rejected",
                data={"reason": "plan_invariant_failed", "error": str(error)},
            )
            await self.store.set_status(
                run.run_id, "failed", terminal_reason="failed"
            )
            return None
        bootstrap_observation = Observation(
            action_id="bootstrap",
            plan_step_id=plan.steps[0].id,
            source="session.get_state",
            succeeded=True,
            summary="Loaded the initial Session state.",
            data=await self._artifact_data(
                run.run_id, "tool_result", bootstrap.output
            ),
        )
        state = DynamicAgentState(
            run_id=run.run_id,
            user_id=DEFAULT_USER_ID,
            session_id=run.resource_id,
            goal_id=_optional_string(bootstrap.output.get("goal_id")),
            knowledge_node_id=_optional_string(
                bootstrap.output.get("knowledge_node_id")
            ),
            plan=plan,
            budget=self.default_budget,
            usage=ledger.usage,
            observations=[bootstrap_observation],
        )
        await self.store.save_plan_version(
            run.run_id, plan.version, plan.model_dump_json()
        )
        await self._save(state)
        await self.store.append_event(
            run.run_id,
            "plan_created",
            data={"plan": plan.model_dump(mode="json")},
        )
        await self.store.append_event(
            run.run_id,
            "observation_recorded",
            node=plan.steps[0].id,
            data=_observation_trace(bootstrap_observation),
        )
        await self.store.append_event(
            run.run_id,
            "budget_updated",
            data=ledger.usage.model_dump(mode="json"),
        )
        return state

    async def _execute_tool_action(
        self,
        run: AgentRun,
        state: DynamicAgentState,
        step: PlanStep,
        action: AgentAction,
        action_id: str,
    ) -> AsyncIterator[AgentEvent]:
        ledger = BudgetLedger(state.budget, state.usage)
        preflight = ledger.preflight("tool")
        if not preflight.allowed:
            await self._save(state)
            yield await self._terminate(
                run.run_id,
                preflight.terminal_reason or "budget_exhausted",
                preflight.detail,
            )
            return
        tool_name = action.tool_name or "unknown"
        started_at = datetime.now(UTC)
        await self.store.start_tool_call(
            call_id=action_id,
            run_id=run.run_id,
            tool_name=tool_name,
            arguments=_redact_arguments(action.arguments),
            started_at=started_at,
        )
        yield await self.store.append_event(
            run.run_id,
            "tool_started",
            node=tool_name,
            data={"call_id": action_id, "plan_step_id": step.id},
        )
        result = await self.tools.execute(
            name=tool_name,
            arguments=action.arguments,
            allowed_tools=set(step.allowed_tools),
            run_id=run.run_id,
            plan_step_id=step.id,
        )
        ledger.record_tool()
        delegated_allocation = _delegated_allocation(tool_name, result)
        if delegated_allocation:
            allocation_decision = ledger.reserve_delegation(delegated_allocation)
            if not allocation_decision.allowed:
                # DelegationService derives allocation from durable parent state, so
                # reaching this branch signals a concurrency/accounting invariant
                # violation and must fail closed before another model turn.
                result = ToolResult(
                    tool_name=tool_name,
                    succeeded=False,
                    error=ToolError(
                        kind=ToolErrorKind.PERMANENT,
                        message=allocation_decision.detail
                        or "delegation exceeded parent budget",
                    ),
                    duration_ms=result.duration_ms,
                )
        summary = _tool_result_summary(result)
        await self.store.finish_tool_call(
            call_id=action_id,
            result_summary=summary,
            status="succeeded" if result.succeeded else "failed",
            duration_ms=result.duration_ms,
            error=result.error.message if result.error else None,
            completed_at=datetime.now(UTC),
        )
        observation = Observation(
            action_id=action_id,
            plan_step_id=step.id,
            source=tool_name,
            succeeded=result.succeeded,
            summary=(
                f"{tool_name} completed successfully"
                if result.succeeded
                else _tool_failure_summary(tool_name, result)
            ),
            data=await self._artifact_data(
                run.run_id, "tool_result", result.output
            ),
            error_kind=result.error.kind if result.error else None,
        )
        state = _append_observation(state, observation, succeeded=result.succeeded)
        state = state.model_copy(update={"usage": ledger.usage})
        await self._save(state)
        yield await self.store.append_event(
            run.run_id,
            "tool_completed",
            node=tool_name,
            data={
                "call_id": action_id,
                **_tool_result_summary(result),
                "duration_ms": result.duration_ms,
            },
        )
        yield await self.store.append_event(
            run.run_id,
            "observation_recorded",
            node=step.id,
            data=_observation_trace(observation),
        )
        yield await self._budget_event(state)

    async def _try_replan(
        self, run: AgentRun, state: DynamicAgentState
    ) -> tuple[DynamicAgentState, AgentEvent, AgentEvent, AgentEvent] | None:
        ledger = BudgetLedger(state.budget, state.usage)
        allowed = ledger.record_replan()
        if not allowed.allowed or not state.observations:
            return None
        step = state.plan.ready_step()
        if step is None:
            return None
        context, context_event = await self._compile_context(
            state,
            step,
            self._allowed_specs(step),
            purpose=ContextPurpose.REPLAN,
        )
        latest = state.observations[-1]
        failure = ToolResult(
            tool_name=latest.source,
            succeeded=False,
            error=ToolError(
                kind=latest.error_kind or ToolErrorKind.PERMANENT,
                message=latest.summary,
                retryable=latest.error_kind
                in {ToolErrorKind.TRANSIENT, ToolErrorKind.TIMEOUT},
            ),
            duration_ms=0,
        )
        token_check = ledger.preflight("model")
        if not token_check.allowed:
            return None
        proposal = await self.policy.replan(
            state=state, context=context, failure=failure
        )
        await self.store.record_context_token_observation(
            context.snapshot.snapshot_id, proposal.usage.input_tokens
        )
        if not ledger.record_model(proposal.usage).allowed:
            return None
        try:
            revised = apply_replan(state.plan, proposal.value)
            _validate_plan_tools(revised, {item.name for item in self._all_specs()})
        except ValueError as error:
            await self.store.append_event(
                run.run_id,
                "plan_rejected",
                data={
                    "reason": "replan_invariant_failed",
                    "error": str(error),
                },
            )
            return None
        state = state.model_copy(
            update={
                "plan": revised,
                "usage": ledger.usage,
                "consecutive_failures": 0,
                "last_action_signature": None,
                "same_action_count": 0,
            }
        )
        await self.store.save_plan_version(
            run.run_id, revised.version, revised.model_dump_json()
        )
        await self._save(state)
        budget_event = await self._budget_event(state)
        replan_event = await self.store.append_event(
            run.run_id,
            "plan_replanned",
            data={
                "version": revised.version,
                "reason": revised.change_reason,
                "plan": revised.model_dump(mode="json"),
            },
        )
        return state, context_event, replan_event, budget_event

    async def _apply_resume(
        self, state: DynamicAgentState, resume: object
    ) -> tuple[DynamicAgentState, Observation]:
        pending = state.pending_interrupt
        if pending is None:
            raise ValueError("dynamic Agent has no pending Interrupt")
        if not isinstance(resume, dict):
            raise ValueError("resume value must be an object")
        supplied_id = resume.get("interrupt_id")
        if supplied_id is not None and supplied_id != pending.id:
            raise ValueError("resume input does not match the active Interrupt")
        value = resume.get("value", resume)
        paused_for = datetime.now(UTC) - pending.created_at
        shifted_start = state.usage.started_at + paused_for
        observation = Observation(
            action_id=f"resume:{pending.id}",
            plan_step_id=pending.plan_step_id,
            source="user.input",
            succeeded=True,
            summary=f"Learner supplied {pending.type} input.",
            data=await self._artifact_data(
                state.run_id,
                "user_input",
                {"input_type": pending.type, "value": value},
            ),
        )
        state = _append_observation(state, observation, succeeded=True)
        state = state.model_copy(
            update={
                "pending_interrupt": None,
                "usage": state.usage.model_copy(update={"started_at": shifted_start}),
            }
        )
        return state, observation

    async def _compile_context(
        self,
        state: DynamicAgentState,
        step: PlanStep,
        tools: list[ToolSpec],
        *,
        purpose: ContextPurpose,
    ) -> tuple[ContextPackage, AgentEvent]:
        """Compile, persist and expose one privacy-safe Context Snapshot.

        Snapshot persistence occurs before the model call, so provider failure still
        leaves the source selection and budget decision available for replay. Full
        Context values are stored only behind the explicit local debug setting.
        """

        request = self.context.request_for(state, step, tools, purpose=purpose)
        package = await self.context.compile(request, state, step, tools)
        event = await self._persist_context(package, node=step.id)
        return package, event

    async def _persist_context(
        self, package: ContextPackage, *, node: str
    ) -> AgentEvent:
        metadata = package.snapshot.model_dump(mode="json")
        await self.store.save_context_snapshot(
            metadata,
            context_values=package.values if self.store_full_context else None,
        )
        event = await self.store.append_event(
            package.snapshot.run_id,
            "context_snapshot_created",
            node=node,
            data={
                "snapshot_id": package.snapshot.snapshot_id,
                "purpose": package.snapshot.purpose,
                "estimated_tokens": package.snapshot.total_input_tokens,
                "input_token_limit": package.snapshot.input_token_limit,
                "reserved_output_tokens": package.snapshot.reserved_output_tokens,
                "source_ids": package.snapshot.source_ids,
                "omitted_source_ids": package.snapshot.omitted_source_ids,
                "tool_names": package.snapshot.tool_names,
                "tokenizer_name": package.snapshot.tokenizer_name,
                "exact_token_count": package.snapshot.exact_token_count,
            },
        )
        return event

    async def _artifact_data(
        self, run_id: str, kind: str, content: object
    ) -> dict[str, object]:
        """Move full Context material out of Agent state into an immutable Artifact."""

        reference = await self.store.save_context_artifact(
            run_id, kind=kind, content=content
        )
        return {"artifact": reference}

    def _validate_action(self, action: AgentAction, step: PlanStep) -> str | None:
        if action.plan_step_id != step.id:
            return "action does not target the active Plan Step"
        if action.action == ActionType.CALL_TOOL:
            definition = self.tools.registry.get(action.tool_name or "")
            if definition is None:
                return "action selected an unregistered Tool"
            if action.tool_name not in step.allowed_tools:
                return "action selected a Tool outside the Step allowlist"
            if not self.allow_write_tools and not definition.spec.read_only:
                return "write Tool is disabled in Shadow mode"
        return None

    def _all_specs(self) -> list[ToolSpec]:
        specs = self.tools.registry.specs()
        visible: list[ToolSpec] = []
        for item in specs:
            definition = self.tools.registry.get(item.name)
            if definition is None:
                continue
            if self.allow_write_tools or definition.spec.read_only:
                visible.append(item)
        return visible

    def _allowed_specs(self, step: PlanStep) -> list[ToolSpec]:
        allowed = self.tools.registry.specs(set(step.allowed_tools))
        if self.allow_write_tools:
            return allowed
        return [item for item in allowed if item.read_only]

    async def _save(self, state: DynamicAgentState) -> None:
        await self.store.save_dynamic_state(state.run_id, state.model_dump_json())

    async def _budget_event(self, state: DynamicAgentState) -> AgentEvent:
        return await self.store.append_event(
            state.run_id,
            "budget_updated",
            data=state.usage.model_dump(mode="json"),
        )

    async def _capture_completed_memory(
        self, state: DynamicAgentState
    ) -> AgentEvent | None:
        """Persist verified Episodic Memory at the Run lifecycle boundary.

        Memory persistence is a secondary durable projection. A database failure is
        recorded but cannot retroactively invalidate a Plan already accepted by the
        Verifier; exact fingerprints make a later replay idempotent.
        """

        if self.memory is None or state.user_id is None or state.session_id is None:
            return None
        try:
            outcome = await self.memory.capture_run_outcome(
                user_id=state.user_id,
                run_id=state.run_id,
                session_id=state.session_id,
                goal_id=state.goal_id,
                knowledge_node_id=state.knowledge_node_id,
                objective=state.plan.objective,
                summary=state.final_summary,
                completed_step_ids=[
                    step.id
                    for step in state.plan.steps
                    if step.status == StepStatus.COMPLETED
                ],
            )
            return await self.store.append_event(
                state.run_id,
                "memory_extracted",
                data={
                    "status": "succeeded",
                    "action": outcome.action,
                    "memory_id": outcome.aggregate.record.id,
                    "memory_status": outcome.aggregate.record.status,
                    "reason": outcome.reason,
                },
            )
        except Exception as error:  # pragma: no cover - operational isolation
            return await self.store.append_event(
                state.run_id,
                "memory_extracted",
                data={
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "message": str(error)[:1_000],
                },
            )

    async def _terminate(
        self, run_id: str, reason: str, detail: str | None = None
    ) -> AgentEvent:
        if reason == "completed":
            await self.store.set_status(
                run_id, "completed", terminal_reason="completed"
            )
            return await self.store.append_event(
                run_id,
                "run_completed",
                data={"terminal_reason": reason, "summary": detail},
            )
        if reason == "cancelled":
            await self.store.set_status(
                run_id, "cancelled", terminal_reason="cancelled"
            )
            return await self.store.append_event(
                run_id, "run_cancelled", data={"terminal_reason": reason}
            )
        terminal = reason if reason in {
            "budget_exhausted",
            "deadline_exceeded",
            "verification_failed",
        } else "failed"
        await self.store.set_status(
            run_id, "failed", terminal_reason=terminal  # type: ignore[arg-type]
        )
        return await self.store.append_event(
            run_id,
            "run_failed",
            data={"terminal_reason": terminal, "message": detail},
        )


def _normalize_and_validate_plan(plan: AgentPlan, allowed_tools: set[str]) -> AgentPlan:
    normalized = plan.model_copy(
        update={
            "version": 1,
            "steps": [
                step.model_copy(update={"status": StepStatus.PENDING})
                for step in plan.steps
            ],
        }
    )
    normalized = AgentPlan.model_validate(normalized.model_dump())
    _validate_plan_tools(normalized, allowed_tools)
    return normalized


def _validate_plan_tools(plan: AgentPlan, allowed_tools: set[str]) -> None:
    selected = {tool for step in plan.steps for tool in step.allowed_tools}
    unknown = selected - allowed_tools
    if unknown:
        raise ValueError(f"Plan references unavailable Tools: {sorted(unknown)}")


def _activate_step(plan: AgentPlan, step_id: str) -> AgentPlan:
    return plan.model_copy(
        update={
            "steps": [
                step.model_copy(
                    update={
                        "status": (
                            StepStatus.ACTIVE if step.id == step_id else step.status
                        ),
                        "attempts": (
                            step.attempts + 1 if step.id == step_id else step.attempts
                        ),
                    }
                )
                for step in plan.steps
            ]
        }
    )


def _complete_step(plan: AgentPlan, step_id: str, evidence_ids: list[str]) -> AgentPlan:
    return plan.model_copy(
        update={
            "steps": [
                step.model_copy(
                    update={
                        "status": StepStatus.COMPLETED,
                        "evidence_ids": list(dict.fromkeys(evidence_ids)),
                    }
                )
                if step.id == step_id
                else step
                for step in plan.steps
            ]
        }
    )


def _step_by_id(plan: AgentPlan, step_id: str) -> PlanStep:
    return next(step for step in plan.steps if step.id == step_id)


def _record_action_signature(
    state: DynamicAgentState, action: AgentAction
) -> tuple[DynamicAgentState, int]:
    signature = action.signature()
    count = (
        state.same_action_count + 1
        if signature == state.last_action_signature
        else 1
    )
    return (
        state.model_copy(
            update={"last_action_signature": signature, "same_action_count": count}
        ),
        count,
    )


def _record_failure(state: DynamicAgentState) -> DynamicAgentState:
    return state.model_copy(
        update={"consecutive_failures": state.consecutive_failures + 1}
    )


def _append_observation(
    state: DynamicAgentState, observation: Observation, *, succeeded: bool
) -> DynamicAgentState:
    return state.model_copy(
        update={
            "observations": [*state.observations, observation][-100:],
            "consecutive_failures": (
                0 if succeeded else state.consecutive_failures + 1
            ),
        }
    )


def _redact_arguments(arguments: dict[str, object]) -> dict[str, object]:
    redacted = dict(arguments)
    selected = redacted.pop("selected_options", None)
    if isinstance(selected, list):
        redacted["selected_option_count"] = len(selected)
    return redacted


def _action_trace(action: AgentAction) -> dict[str, object]:
    payload = action.model_dump(mode="json")
    arguments = payload.get("arguments")
    if isinstance(arguments, dict):
        payload["arguments"] = _redact_arguments(arguments)
    return payload


def _observation_trace(observation: Observation) -> dict[str, object]:
    """Trace 只保存脱敏摘要和 keys；完整值只存在 durable state。"""

    return {
        "id": observation.id,
        "action_id": observation.action_id,
        "plan_step_id": observation.plan_step_id,
        "source": observation.source,
        "succeeded": observation.succeeded,
        "summary": observation.summary,
        "data_keys": sorted(observation.data),
        "error_kind": observation.error_kind,
        "created_at": observation.created_at.isoformat(),
    }


def _tool_result_summary(result: ToolResult) -> dict[str, object]:
    return {
        "succeeded": result.succeeded,
        "truncated": result.truncated,
        "output_type": type(result.output).__name__,
        "error_kind": result.error.kind if result.error else None,
    }


def _tool_failure_summary(tool_name: str, result: ToolResult) -> str:
    message = result.error.message if result.error is not None else "unknown"
    return f"{tool_name} failed: {message}"


def _delegated_allocation(tool_name: str, result: ToolResult) -> int:
    # Only the trusted delegation namespace may affect the parent reservation.
    # Arbitrary retrieved Tool data containing an ``allocated_tokens`` field is
    # untrusted and must not be able to exhaust the Lead's budget.
    if not tool_name.startswith("delegate.") or not isinstance(result.output, dict):
        return 0
    usage = result.output.get("usage")
    if not isinstance(usage, dict):
        return 0
    value = usage.get("allocated_tokens")
    return value if isinstance(value, int) and value > 0 else 0


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
