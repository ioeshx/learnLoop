"""Typed Tool Registry and guarded Tool Executor for the v2 Agent."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.delegation import (
    DelegationExecutionError,
    DelegationService,
    DelegationStatus,
)
from app.agent.dynamic.models import (
    ApprovalPolicy,
    ToolError,
    ToolErrorKind,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from app.agent.research import ResearchTutor
from app.agent.tools import LearningTools
from app.application.errors import ApplicationError


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GoalStateInput(ToolInput):
    goal_id: str = Field(min_length=1)


class SessionStateInput(ToolInput):
    session_id: str = Field(min_length=1)


class MasteryInput(ToolInput):
    knowledge_node_id: str = Field(min_length=1)


class ResourceSearchInput(ToolInput):
    knowledge_node_id: str = Field(min_length=1)


class ExerciseInput(ToolInput):
    session_id: str = Field(min_length=1)


class GradeExerciseInput(ToolInput):
    session_id: str = Field(min_length=1)
    exercise_id: str = Field(min_length=1)
    selected_options: list[str] = Field(min_length=1, max_length=6)


class ScheduleReviewInput(ToolInput):
    due_at: str = Field(min_length=1)


class ResearchInput(ToolInput):
    question: str = Field(min_length=1, max_length=4_000)
    goal_id: str = Field(min_length=1)
    knowledge_node_id: str | None = None


class DelegateResearchInput(ToolInput):
    objective: str = Field(min_length=1, max_length=2_000)


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Trusted execution metadata injected by ToolExecutor, never by the model."""

    run_id: str
    plan_step_id: str
    idempotency_key: str


ToolHandler = Callable[[BaseModel, ToolInvocation], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    spec: ToolSpec
    input_type: type[BaseModel]
    handler: ToolHandler


class ToolRegistry:
    """集中维护 Tool Contract，避免把任意 Python method 暴露给模型。"""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        input_type: type[BaseModel],
        handler: ToolHandler,
        risk: ToolRisk = ToolRisk.LOW,
        approval_policy: ApprovalPolicy = ApprovalPolicy.NEVER,
        read_only: bool = True,
        idempotent: bool = True,
        timeout_seconds: float = 10.0,
        max_result_chars: int = 8_000,
    ) -> None:
        if name in self._definitions:
            raise ValueError(f"tool '{name}' is already registered")
        spec = ToolSpec(
            name=name,
            description=description,
            risk=risk,
            approval_policy=approval_policy,
            read_only=read_only,
            idempotent=idempotent,
            timeout_seconds=timeout_seconds,
            max_result_chars=max_result_chars,
            input_schema=input_type.model_json_schema(),
        )
        self._definitions[name] = ToolDefinition(spec, input_type, handler)

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def specs(self, allowed_names: set[str] | None = None) -> list[ToolSpec]:
        names = allowed_names if allowed_names is not None else set(self._definitions)
        return [
            definition.spec
            for name, definition in sorted(self._definitions.items())
            if name in names
        ]


class ToolExecutor:
    """执行 validation → allowlist → approval → timeout → truncation → error mapping。

    幂等键由 ``run/step/tool/arguments`` 的稳定 hash 生成。相同逻辑动作在进程崩溃后
    重放仍得到同一个 key，而用户修改答案后 arguments 改变，会自然生成新的 key。
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        allowed_tools: set[str],
        run_id: str,
        plan_step_id: str,
        approved: bool = False,
    ) -> ToolResult:
        started = perf_counter()
        definition = self.registry.get(name)
        if definition is None:
            return self._error(
                name,
                ToolErrorKind.PERMISSION_DENIED,
                "tool is not registered",
                started,
            )
        if name not in allowed_tools:
            return self._error(
                name,
                ToolErrorKind.PERMISSION_DENIED,
                "tool is outside the current Plan Step allowlist",
                started,
            )
        if definition.spec.approval_policy == ApprovalPolicy.ALWAYS and not approved:
            return self._error(
                name,
                ToolErrorKind.PERMISSION_DENIED,
                "tool requires explicit approval",
                started,
            )
        try:
            validated = definition.input_type.model_validate(arguments)
        except ValidationError as error:
            return self._error(
                name,
                ToolErrorKind.INVALID_ARGUMENTS,
                str(error),
                started,
            )

        invocation = ToolInvocation(
            run_id=run_id,
            plan_step_id=plan_step_id,
            idempotency_key=_idempotency_key(
                run_id, plan_step_id, name, arguments
            ),
        )
        try:
            async with asyncio.timeout(definition.spec.timeout_seconds):
                raw_output = await definition.handler(validated, invocation)
        except TimeoutError:
            return self._error(
                name,
                ToolErrorKind.TIMEOUT,
                "tool execution exceeded its timeout",
                started,
                retryable=True,
            )
        except DelegationExecutionError as error:
            kind = {
                DelegationStatus.DEADLINE_EXCEEDED: ToolErrorKind.TIMEOUT,
                DelegationStatus.CANCELLED: ToolErrorKind.CANCELLED,
            }.get(error.result.status, ToolErrorKind.PERMANENT)
            return ToolResult(
                tool_name=name,
                succeeded=False,
                output=error.result.model_dump(mode="json"),
                error=ToolError(
                    kind=kind,
                    message=f"Researcher Subagent {error.result.status.value}",
                    retryable=False,
                ),
                duration_ms=(perf_counter() - started) * 1_000,
            )
        except ApplicationError as error:
            kind = {
                "not_found": ToolErrorKind.NOT_FOUND,
                "conflict": ToolErrorKind.CONFLICT,
            }.get(error.code, ToolErrorKind.PERMANENT)
            return self._error(name, kind, error.message, started)
        except (ValueError, TypeError) as error:
            return self._error(
                name, ToolErrorKind.PERMANENT, str(error), started
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # pragma: no cover - defensive adapter boundary
            return self._error(
                name,
                ToolErrorKind.TRANSIENT,
                f"{type(error).__name__}: {str(error)[:1_000]}",
                started,
                retryable=True,
            )

        output, truncated = _bounded_output(
            raw_output, definition.spec.max_result_chars
        )
        return ToolResult(
            tool_name=name,
            succeeded=True,
            output=output,
            truncated=truncated,
            duration_ms=(perf_counter() - started) * 1_000,
        )

    @staticmethod
    def _error(
        name: str,
        kind: ToolErrorKind,
        message: str,
        started: float,
        *,
        retryable: bool = False,
    ) -> ToolResult:
        return ToolResult(
            tool_name=name,
            succeeded=False,
            error=ToolError(kind=kind, message=message[:2_000], retryable=retryable),
            duration_ms=(perf_counter() - started) * 1_000,
        )


def build_learning_tool_registry(
    tools: LearningTools,
    research: ResearchTutor | None = None,
    delegation: DelegationService | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()

    async def goal_state(value: BaseModel, _: ToolInvocation) -> object:
        parsed = GoalStateInput.model_validate(value)
        return await tools.get_learning_goal(parsed.goal_id)

    async def session_state(value: BaseModel, _: ToolInvocation) -> object:
        parsed = SessionStateInput.model_validate(value)
        return await tools.get_study_session(parsed.session_id)

    async def mastery(value: BaseModel, _: ToolInvocation) -> object:
        parsed = MasteryInput.model_validate(value)
        return await tools.get_mastery_state(parsed.knowledge_node_id)

    async def search(value: BaseModel, _: ToolInvocation) -> object:
        parsed = ResourceSearchInput.model_validate(value)
        return await tools.search_learning_resources(parsed.knowledge_node_id)

    async def exercise(value: BaseModel, _: ToolInvocation) -> object:
        parsed = ExerciseInput.model_validate(value)
        return await tools.create_exercise(parsed.session_id)

    async def grade(value: BaseModel, invocation: ToolInvocation) -> object:
        parsed = GradeExerciseInput.model_validate(value)
        return await tools.update_mastery(
            session_id=parsed.session_id,
            exercise_id=parsed.exercise_id,
            selected_options=parsed.selected_options,
            idempotency_key=invocation.idempotency_key,
        )

    async def schedule(value: BaseModel, _: ToolInvocation) -> object:
        parsed = ScheduleReviewInput.model_validate(value)
        return {"due_at": tools.schedule_review({"due_at": parsed.due_at})}

    async def complete(value: BaseModel, _: ToolInvocation) -> object:
        parsed = SessionStateInput.model_validate(value)
        await tools.complete_study_session(parsed.session_id)
        return {"session_id": parsed.session_id, "status": "completed"}

    async def ask_research(value: BaseModel, _: ToolInvocation) -> object:
        if research is None:
            raise RuntimeError("Research Tutor is not configured")
        parsed = ResearchInput.model_validate(value)
        result = await research.run(
            research.request_for(
                parsed.question,
                goal_id=parsed.goal_id,
                knowledge_node_id=parsed.knowledge_node_id,
            )
        )
        # The durable Research Trace keeps every rejected Chunk for audit. The Tool
        # boundary deliberately projects only accepted Evidence into Agent Context,
        # so low-quality or prompt-injection text cannot become an Observation.
        accepted_evidence = [
            item
            for item in result.evidence
            if item.grade.verdict.value == "accepted"
        ]
        return {
            "trace_id": result.trace_id,
            "mode": result.mode.value,
            "status": result.status,
            "answer": result.answer,
            "claims": [
                item.model_dump(mode="json")
                for item in result.claims
                if item.included_in_answer
            ],
            "citations": [
                item.model_dump(mode="json")
                for item in result.citations
                if item.status.value != "unsupported"
            ],
            "evidence": [
                item.model_dump(mode="json") for item in accepted_evidence
            ],
            "gaps": result.gaps,
            "usage": result.usage.model_dump(mode="json"),
        }

    async def delegate_research(
        value: BaseModel, invocation: ToolInvocation
    ) -> object:
        if delegation is None:
            raise RuntimeError("Subagent delegation is not configured")
        parsed = DelegateResearchInput.model_validate(value)
        result = await delegation.delegate_research(
            parent_run_id=invocation.run_id,
            plan_step_id=invocation.plan_step_id,
            objective=parsed.objective,
        )
        if result.status in {
            DelegationStatus.FAILED,
            DelegationStatus.DEADLINE_EXCEEDED,
            DelegationStatus.CANCELLED,
        }:
            raise DelegationExecutionError(result)
        return result.model_dump(mode="json")

    registry.register(
        name="goal.get_state",
        description="Read a learning goal and its constraints.",
        input_type=GoalStateInput,
        handler=goal_state,
    )
    registry.register(
        name="session.get_state",
        description="Read the current study Session, lesson and exercise.",
        input_type=SessionStateInput,
        handler=session_state,
    )
    registry.register(
        name="learner.get_mastery",
        description="Read mastery evidence for one knowledge node.",
        input_type=MasteryInput,
        handler=mastery,
    )
    registry.register(
        name="resource.search",
        description="Search the learner's local resources for grounded evidence.",
        input_type=ResourceSearchInput,
        handler=search,
        max_result_chars=12_000,
    )
    if research is not None:
        registry.register(
            name="research.ask",
            description=(
                "Run bounded multi-step research over local learner resources and "
                "return verified Claim-to-Chunk citations."
            ),
            input_type=ResearchInput,
            handler=ask_research,
            max_result_chars=20_000,
        )
    if delegation is not None:
        registry.register(
            name="delegate.research",
            description=(
                "Delegate one complex multi-hop, read-only research task to an "
                "isolated Researcher child Agent. Simple questions must use "
                "research.ask."
            ),
            input_type=DelegateResearchInput,
            handler=delegate_research,
            timeout_seconds=min(120, delegation.deadline_seconds + 5),
            max_result_chars=20_000,
        )
    registry.register(
        name="exercise.get",
        description="Read the exercise already bound to the current Session.",
        input_type=ExerciseInput,
        handler=exercise,
    )
    registry.register(
        name="exercise.grade",
        description="Persist deterministic grading, mastery and review updates.",
        input_type=GradeExerciseInput,
        handler=grade,
        risk=ToolRisk.MEDIUM,
        read_only=False,
        idempotent=True,
    )
    registry.register(
        name="review.schedule",
        description="Validate and expose the review date produced by grading.",
        input_type=ScheduleReviewInput,
        handler=schedule,
        risk=ToolRisk.MEDIUM,
        read_only=False,
        idempotent=True,
    )
    registry.register(
        name="session.complete",
        description="Complete the Session after a persisted answer exists.",
        input_type=SessionStateInput,
        handler=complete,
        risk=ToolRisk.MEDIUM,
        read_only=False,
        idempotent=True,
    )
    return registry


def _idempotency_key(
    run_id: str, step_id: str, tool_name: str, arguments: dict[str, Any]
) -> str:
    canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(
        f"{run_id}:{step_id}:{tool_name}:{canonical}".encode()
    ).hexdigest()
    return f"dynamic-agent:{digest}"


def _bounded_output(value: object, max_chars: int) -> tuple[Any, bool]:
    decoded = json.loads(json.dumps(value, ensure_ascii=False, default=str))
    serialized = json.dumps(decoded, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return decoded, False
    # 截断后的 Tool result 只作为 Observation 摘要，绝不伪装成完整 JSON 数据。
    return {"summary": serialized[:max_chars], "truncated": True}, True
