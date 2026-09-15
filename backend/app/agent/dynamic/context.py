"""Stage 12 Context Engine for the bounded LearnLoop v2 Agent."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import ceil
from typing import Protocol, cast
from uuid import uuid4

from pydantic import Field, model_validator

from app.agent.dynamic.models import (
    AgentContract,
    BudgetUsage,
    DynamicAgentState,
    Observation,
    PlanStep,
    RunBudget,
    StepStatus,
    ToolSpec,
)
from app.agent.memory.models import MemoryQuery, MemoryRecall


class ContextPurpose(StrEnum):
    PLANNER = "planner"
    DECISION = "decision"
    REPLAN = "replan"


class ContextReferenceError(RuntimeError):
    """Raised when an evidence or Artifact reference is no longer reproducible."""


class ContextBudgetError(RuntimeError):
    """Raised when mandatory partitions cannot fit inside the input budget."""


class ContextRequest(AgentContract):
    """一次 Context 编译的结构化输入，而不是预先拼好的 Prompt 字符串。"""

    run_id: str
    plan_version: int = Field(ge=0)
    step_id: str
    objective: str
    purpose: ContextPurpose = ContextPurpose.DECISION
    recent_observation_ids: list[str] = Field(default_factory=list, max_length=100)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    memory_query: str | None = None
    candidate_tool_names: list[str] = Field(default_factory=list, max_length=32)
    token_budget: int = Field(ge=129)
    reserved_output_tokens: int = Field(ge=128)

    @model_validator(mode="after")
    def validate_reservation(self) -> ContextRequest:
        if self.reserved_output_tokens >= self.token_budget:
            raise ValueError("reserved output must leave positive input capacity")
        return self


class ContextArtifactRef(AgentContract):
    artifact_id: str
    kind: str
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    expires_at: datetime | None = None


class ContextPartitionUsage(AgentContract):
    name: str
    priority: int = Field(ge=0, le=100)
    mandatory: bool
    token_count: int = Field(ge=0)
    item_count: int = Field(ge=0)


class ContextTruncation(AgentContract):
    partition: str
    reason: str
    omitted_source_ids: list[str] = Field(default_factory=list)


class ContextConflict(AgentContract):
    source: str
    field: str
    source_ids: list[str] = Field(min_length=2)


class ContextSnapshot(AgentContract):
    """可持久化的 Context provenance；默认不包含敏感的完整 Context。"""

    snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    purpose: ContextPurpose
    plan_version: int
    step_id: str
    token_budget: int
    reserved_output_tokens: int
    input_token_limit: int
    total_input_tokens: int
    tokenizer_name: str
    exact_token_count: bool
    partitions: list[ContextPartitionUsage]
    source_ids: list[str]
    omitted_source_ids: list[str]
    tool_names: list[str]
    truncations: list[ContextTruncation]
    conflicts: list[ContextConflict]
    compaction_version: int = 1
    input_hash: str
    output_hash: str
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    observed_model_input_tokens: int | None = None
    token_delta: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ContextPackage:
    values: dict[str, object]
    snapshot: ContextSnapshot

    # 这些兼容属性让 Stage 11 Policy contract 保持稳定，同时把实现升级为 Stage 12。
    @property
    def estimated_tokens(self) -> int:
        return self.snapshot.total_input_tokens

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(self.snapshot.source_ids)

    @property
    def truncated_observations(self) -> int:
        return len(self.snapshot.omitted_source_ids)


class TokenCounter(Protocol):
    name: str
    exact: bool

    def count_text(self, value: str) -> int: ...


class ContextArtifactReader(Protocol):
    async def get_context_artifact(
        self, artifact_id: str
    ) -> dict[str, object] | None: ...


class MemoryRetriever(Protocol):
    async def retrieve(self, query: MemoryQuery) -> list[MemoryRecall]: ...


class ConservativeTokenCounter:
    """Provider tokenizer 不可用时的保守 Unicode token estimation。"""

    name = "unicode_chars_div_3"
    exact = False

    def count_text(self, value: str) -> int:
        return ceil(len(value) / 3)


class CallableTokenCounter:
    """Adapter for a model provider exposing a synchronous ``count_tokens`` API."""

    exact = True

    def __init__(self, name: str, counter: Callable[[str], int]) -> None:
        self.name = name
        self._counter = counter

    def count_text(self, value: str) -> int:
        count = self._counter(value)
        if count < 0:
            raise ValueError("provider tokenizer returned a negative token count")
        return count


def token_counter_for(provider: object | None) -> TokenCounter:
    """Prefer the active provider tokenizer, otherwise return a traceable fallback."""

    method = getattr(provider, "count_tokens", None)
    if callable(method):
        provider_name = str(getattr(provider, "name", type(provider).__name__))
        return CallableTokenCounter(
            f"{provider_name}.count_tokens", cast(Callable[[str], int], method)
        )
    return ConservativeTokenCounter()


class ContextCompiler:
    """Compile budgeted, provenance-preserving Context for one Agent decision.

    编译器先固定 policy/objective/current Step/unresolved state/Budget/Tool Schema 等
    mandatory partitions，再按 evidence、recent Observation、completed Step 的优先级
    装箱。
    任何 optional eviction 都形成 ``ContextTruncation``；关键 evidence 或唯一合法 Tool
    无法放入时则明确失败，禁止 silent truncation。
    """

    def __init__(
        self,
        *,
        artifact_reader: ContextArtifactReader | None = None,
        memory_retriever: MemoryRetriever | None = None,
        token_counter: TokenCounter | None = None,
        max_context_tokens: int = 12_000,
        reserved_output_tokens: int = 2_048,
        max_recent_observations: int = 24,
        source_ttl_seconds: int = 86_400,
        memory_enabled: bool = True,
        memory_limit: int = 6,
        memory_minimum_score: float = 0.24,
    ) -> None:
        if max_context_tokens < 1_000:
            raise ValueError("max_context_tokens must be at least 1000")
        if not 128 <= reserved_output_tokens < max_context_tokens:
            raise ValueError("reserved output must be smaller than context budget")
        if max_recent_observations < 1:
            raise ValueError("max_recent_observations must be positive")
        if source_ttl_seconds < 60:
            raise ValueError("source_ttl_seconds must be at least 60")
        if not 1 <= memory_limit <= 20:
            raise ValueError("memory_limit must be between 1 and 20")
        if not 0 <= memory_minimum_score <= 1:
            raise ValueError("memory_minimum_score must be between 0 and 1")
        self.artifact_reader = artifact_reader
        self.memory_retriever = memory_retriever
        self.token_counter = token_counter or ConservativeTokenCounter()
        self.max_context_tokens = max_context_tokens
        self.reserved_output_tokens = reserved_output_tokens
        self.max_recent_observations = max_recent_observations
        self.source_ttl_seconds = source_ttl_seconds
        self.memory_enabled = memory_enabled
        self.memory_limit = memory_limit
        self.memory_minimum_score = memory_minimum_score

    def request_for(
        self,
        state: DynamicAgentState,
        step: PlanStep,
        tools: list[ToolSpec],
        *,
        purpose: ContextPurpose = ContextPurpose.DECISION,
    ) -> ContextRequest:
        evidence_ids = [
            evidence_id
            for plan_step in state.plan.steps
            for evidence_id in plan_step.evidence_ids
        ]
        recent_ids = [
            item.id for item in state.observations[-self.max_recent_observations :]
        ]
        token_budget, output_reserve = self._remaining_call_budget(
            state.budget, state.usage
        )
        return ContextRequest(
            run_id=state.run_id,
            plan_version=state.plan.version,
            step_id=step.id,
            objective=state.plan.objective,
            purpose=purpose,
            recent_observation_ids=recent_ids,
            evidence_ids=evidence_ids,
            memory_query=(
                f"{state.plan.objective}\nCurrent Step: {step.objective}\n"
                "Relevant learning preference, prior episode, and procedure"
                if self.memory_enabled and state.user_id is not None
                else None
            ),
            candidate_tool_names=[item.name for item in tools],
            token_budget=token_budget,
            reserved_output_tokens=output_reserve,
        )

    def initial_request(
        self,
        run_id: str,
        objective: str,
        tools: list[ToolSpec],
        budget: RunBudget,
        usage: BudgetUsage,
    ) -> ContextRequest:
        token_budget, output_reserve = self._remaining_call_budget(budget, usage)
        return ContextRequest(
            run_id=run_id,
            plan_version=0,
            step_id="planner",
            objective=objective,
            purpose=ContextPurpose.PLANNER,
            candidate_tool_names=[item.name for item in tools],
            token_budget=token_budget,
            reserved_output_tokens=output_reserve,
        )

    def compile_initial(
        self,
        request: ContextRequest,
        initial_state: dict[str, object],
        tools: list[ToolSpec],
        *,
        max_plan_steps: int,
        candidate_skills: list[dict[str, object]] | None = None,
        prior_reflections: list[dict[str, object]] | None = None,
    ) -> ContextPackage:
        """Compile the Planner input through the same partition and budget contract."""

        if request.purpose != ContextPurpose.PLANNER:
            raise ValueError("initial Context must use planner purpose")
        available = {item.name: item for item in tools}
        selected = [
            available[name]
            for name in request.candidate_tool_names
            if name in available
        ]
        values: dict[str, object] = {
            "policy": {
                "scope": "single_learning_session",
                "untrusted_sources": True,
                "tool_permissions_are_code_enforced": True,
            },
            "objective": request.objective,
            "initial_state": initial_state,
            "available_tools": [_compact_tool(item) for item in selected],
            "max_steps": max_plan_steps,
            "candidate_skills": [],
            "prior_reflections": [],
        }
        input_limit = request.token_budget - request.reserved_output_tokens
        skill_items = candidate_skills or []
        kept_skills, omitted_skills = self._pack_items(
            values, "candidate_skills", skill_items, input_limit
        )
        values["candidate_skills"] = kept_skills
        reflection_items = prior_reflections or []
        kept_reflections, omitted_reflections = self._pack_items(
            values, "prior_reflections", reflection_items, input_limit
        )
        values["prior_reflections"] = kept_reflections
        total_tokens = self._count(values)
        if total_tokens > input_limit:
            raise ContextBudgetError(
                "Planner Context exceeds the configured input token limit"
            )
        source_id = "bootstrap:session.get_state"
        snapshot = ContextSnapshot(
            run_id=request.run_id,
            purpose=request.purpose,
            plan_version=0,
            step_id=request.step_id,
            token_budget=request.token_budget,
            reserved_output_tokens=request.reserved_output_tokens,
            input_token_limit=input_limit,
            total_input_tokens=total_tokens,
            tokenizer_name=self.token_counter.name,
            exact_token_count=self.token_counter.exact,
            partitions=[
                ContextPartitionUsage(
                    name=name,
                    priority=(90 if name == "prior_reflections" else 85)
                    if name in {"prior_reflections", "candidate_skills"}
                    else 100,
                    mandatory=name not in {"prior_reflections", "candidate_skills"},
                    token_count=self._count(value),
                    item_count=len(value) if isinstance(value, list) else 1,
                )
                for name, value in values.items()
            ],
            source_ids=[
                source_id,
                *[f"skill:{item['id']}@{item['version']}" for item in kept_skills],
                *[f"reflection:{item['id']}" for item in kept_reflections],
            ],
            omitted_source_ids=[*omitted_skills, *omitted_reflections],
            tool_names=[item.name for item in selected],
            truncations=(
                [
                    ContextTruncation(
                        partition="candidate_skills",
                        reason="input_token_budget",
                        omitted_source_ids=omitted_skills,
                    )
                ]
                if omitted_skills
                else []
            )
            + (
                [
                    ContextTruncation(
                        partition="prior_reflections",
                        reason="input_token_budget",
                        omitted_source_ids=omitted_reflections,
                    )
                ]
                if omitted_reflections
                else []
            ),
            conflicts=[],
            input_hash=_stable_hash(
                {
                    "request": request.model_dump(mode="json"),
                    "initial_state": initial_state,
                }
            ),
            output_hash=_stable_hash(values),
        )
        return ContextPackage(values=values, snapshot=snapshot)

    async def compile(
        self,
        request: ContextRequest,
        state: DynamicAgentState,
        step: PlanStep,
        tools: list[ToolSpec],
        applied_skill: dict[str, object] | None = None,
    ) -> ContextPackage:
        if request.run_id != state.run_id:
            raise ContextReferenceError("ContextRequest belongs to another Run")
        if request.plan_version != state.plan.version or request.step_id != step.id:
            raise ContextReferenceError("ContextRequest references stale Plan state")

        selected_tools = self._select_tools(request, step, tools)
        observation_by_id = {item.id: item for item in state.observations}
        missing_evidence = set(request.evidence_ids) - set(observation_by_id)
        if missing_evidence:
            raise ContextReferenceError(
                f"evidence Observations are missing: {sorted(missing_evidence)}"
            )

        requested_ids = list(
            dict.fromkeys(
                [*request.evidence_ids, *request.recent_observation_ids]
            )
        )
        resolved: list[dict[str, object]] = []
        omitted: list[str] = []
        expired: list[str] = []
        truncations: list[ContextTruncation] = []
        for source_id in requested_ids:
            observation = observation_by_id.get(source_id)
            if observation is None:
                omitted.append(source_id)
                continue
            materialized = await self._materialize(
                observation,
                expected_run_id=state.run_id,
                required=source_id in request.evidence_ids,
            )
            if materialized is None:
                omitted.append(source_id)
                expired.append(source_id)
                continue
            resolved.append(materialized)

        if expired:
            truncations.append(
                ContextTruncation(
                    partition="recent_observations",
                    reason="source_expired",
                    omitted_source_ids=expired,
                )
            )
        evidence_set = set(request.evidence_ids)
        resolved, duplicate_ids = _deduplicate_observations(
            resolved, protected_ids=evidence_set
        )
        if duplicate_ids:
            omitted.extend(duplicate_ids)
            truncations.append(
                ContextTruncation(
                    partition="recent_observations",
                    reason="duplicate_observation_compacted",
                    omitted_source_ids=duplicate_ids,
                )
            )
        conflicts = _detect_conflicts(resolved)
        evidence = [item for item in resolved if str(item["id"]) in evidence_set]
        recent = [item for item in resolved if str(item["id"]) not in evidence_set]

        values: dict[str, object] = {
            "policy": {
                "untrusted_sources": True,
                "tool_permissions_are_code_enforced": True,
                "hidden_reasoning_must_not_be_emitted": True,
            },
            "objective": request.objective,
            "plan_version": request.plan_version,
            "current_step": step.model_dump(mode="json"),
            "unresolved_items": _unresolved_items(state),
            "budget": state.budget.model_dump(mode="json"),
            "usage": state.usage.model_dump(mode="json"),
            "available_tools": [_compact_tool(item) for item in selected_tools],
            "evidence": [_evidence_view(item) for item in evidence],
            "applied_skill": applied_skill,
            "memory": [],
            "recent_observations": [],
            "completed_steps": [],
            "conflicts": [],
        }
        input_limit = request.token_budget - request.reserved_output_tokens
        if input_limit < 1:
            raise ContextBudgetError("reserved output consumes the Context budget")
        mandatory_tokens = self._count(values)
        if mandatory_tokens > input_limit:
            raise ContextBudgetError(
                "mandatory Context partitions exceed the input token limit"
            )

        memory_items = await self._retrieve_memory(request, state)
        memory_kept, memory_omitted = self._pack_items(
            values, "memory", memory_items, input_limit
        )
        values["memory"] = memory_kept
        if memory_omitted:
            omitted.extend(memory_omitted)
            truncations.append(
                ContextTruncation(
                    partition="memory",
                    reason="input_token_budget",
                    omitted_source_ids=memory_omitted,
                )
            )
        for item in memory_kept:
            raw_conflicts = item.get("conflicting_memory_ids", [])
            if isinstance(raw_conflicts, list) and raw_conflicts:
                conflicts.append(
                    ContextConflict(
                        source="agent_memory",
                        field=str(item.get("memory_key", "unknown")),
                        source_ids=[
                            str(item["id"]), *[str(value) for value in raw_conflicts]
                        ],
                    )
                )

        recent_kept, recent_omitted = self._pack_items(
            values, "recent_observations", recent, input_limit
        )
        if recent_omitted:
            omitted.extend(recent_omitted)
            truncations.append(
                ContextTruncation(
                    partition="recent_observations",
                    reason="input_token_budget",
                    omitted_source_ids=recent_omitted,
                )
            )
        values["recent_observations"] = recent_kept

        completed = [
            {
                "id": item.id,
                "objective": item.objective,
                "status": item.status,
                "evidence_ids": item.evidence_ids,
                "attempts": item.attempts,
            }
            for item in state.plan.steps
            if item.status == StepStatus.COMPLETED
        ]
        completed_kept, completed_omitted = self._pack_items(
            values, "completed_steps", completed, input_limit
        )
        values["completed_steps"] = completed_kept
        if completed_omitted:
            truncations.append(
                ContextTruncation(
                    partition="completed_steps",
                    reason="compacted_to_evidence_references",
                    omitted_source_ids=completed_omitted,
                )
            )
        conflict_values = [item.model_dump(mode="json") for item in conflicts]
        conflict_kept, _ = self._pack_items(
            values, "conflicts", conflict_values, input_limit
        )
        values["conflicts"] = conflict_kept

        included_ids = [
            str(item["id"])
            for item in [*evidence, *recent_kept]
            if "id" in item
        ]
        included_ids.extend(str(item["id"]) for item in memory_kept)
        timestamps = [
            datetime.fromisoformat(str(item["created_at"]))
            for item in [*evidence, *recent_kept]
            if item.get("created_at")
        ]
        total_tokens = self._count(values)
        partitions = [
            ContextPartitionUsage(
                name=name,
                priority=_PARTITION_PRIORITIES.get(name, 0),
                mandatory=name in _MANDATORY_PARTITIONS,
                token_count=self._count(value),
                item_count=len(value) if isinstance(value, list) else 1,
            )
            for name, value in values.items()
        ]
        snapshot = ContextSnapshot(
            run_id=request.run_id,
            purpose=request.purpose,
            plan_version=request.plan_version,
            step_id=request.step_id,
            token_budget=request.token_budget,
            reserved_output_tokens=request.reserved_output_tokens,
            input_token_limit=input_limit,
            total_input_tokens=total_tokens,
            tokenizer_name=self.token_counter.name,
            exact_token_count=self.token_counter.exact,
            partitions=partitions,
            source_ids=list(dict.fromkeys(included_ids)),
            omitted_source_ids=list(dict.fromkeys(omitted)),
            tool_names=[item.name for item in selected_tools],
            truncations=truncations,
            conflicts=conflicts,
            input_hash=_stable_hash(
                {
                    "request": request.model_dump(mode="json"),
                    "sources": [
                        {"id": item.get("id"), "hash": item.get("content_hash")}
                        for item in resolved
                    ]
                    + [
                        {"id": item["id"], "hash": _stable_hash(item)}
                        for item in memory_kept
                    ],
                }
            ),
            output_hash=_stable_hash(values),
            coverage_start=min(timestamps) if timestamps else None,
            coverage_end=max(timestamps) if timestamps else None,
        )
        return ContextPackage(values=values, snapshot=snapshot)

    async def _retrieve_memory(
        self, request: ContextRequest, state: DynamicAgentState
    ) -> list[dict[str, object]]:
        """Retrieve only Active Memory and preserve Evidence in the Context view.

        Memory is an optional partition and never weakens mandatory Context or Tool
        permission invariants.
        """

        if (
            not self.memory_enabled
            or self.memory_retriever is None
            or state.user_id is None
            or request.memory_query is None
        ):
            return []
        recalls = await self.memory_retriever.retrieve(
            MemoryQuery(
                user_id=state.user_id,
                text=request.memory_query,
                goal_id=state.goal_id,
                knowledge_node_id=state.knowledge_node_id,
                limit=self.memory_limit,
                minimum_score=self.memory_minimum_score,
            )
        )
        return [
            {
                "id": f"memory:{item.memory_id}",
                "memory_id": item.memory_id,
                "memory_key": item.memory_key,
                "kind": item.kind,
                "content": item.content,
                "attributes": item.attributes,
                "score": item.score,
                "confidence": item.confidence,
                "importance": item.importance,
                "trust": item.trust,
                "valid_from": item.valid_from.isoformat(),
                "expires_at": (
                    item.expires_at.isoformat() if item.expires_at else None
                ),
                "evidence": item.evidence,
                "conflicting_memory_ids": item.conflicting_memory_ids,
            }
            for item in recalls
        ]

    def _select_tools(
        self, request: ContextRequest, step: PlanStep, tools: list[ToolSpec]
    ) -> list[ToolSpec]:
        available = {item.name: item for item in tools}
        requested = set(request.candidate_tool_names)
        allowed = set(step.allowed_tools)
        selected_names = requested & allowed & set(available)
        missing = requested & allowed - set(available)
        if missing:
            raise ContextReferenceError(
                f"candidate Tool schemas are unavailable: {sorted(missing)}"
            )
        return [available[name] for name in sorted(selected_names)]

    async def _materialize(
        self,
        observation: Observation,
        *,
        expected_run_id: str,
        required: bool,
    ) -> dict[str, object] | None:
        record = observation.model_dump(mode="json")
        data = observation.data
        age_seconds = (datetime.now(UTC) - observation.created_at).total_seconds()
        if not required and age_seconds > self.source_ttl_seconds:
            return None
        raw_ref = data.get("artifact") if isinstance(data, dict) else None
        if not isinstance(raw_ref, dict):
            record["content_hash"] = _stable_hash(data)
            return record
        reference = ContextArtifactRef.model_validate(raw_ref)
        now = datetime.now(UTC)
        if reference.expires_at is not None and reference.expires_at <= now:
            if required:
                raise ContextReferenceError(
                    f"required Artifact {reference.artifact_id} has expired"
                )
            return None
        if self.artifact_reader is None:
            if required:
                raise ContextReferenceError("Artifact reader is not configured")
            return None
        stored = await self.artifact_reader.get_context_artifact(
            reference.artifact_id
        )
        if stored is None:
            raise ContextReferenceError(
                f"Artifact {reference.artifact_id} no longer exists"
            )
        if str(stored["run_id"]) != expected_run_id:
            raise ContextReferenceError(
                f"Artifact {reference.artifact_id} belongs to another Run"
            )
        if int(str(stored["version"])) != reference.version:
            raise ContextReferenceError(
                f"Artifact {reference.artifact_id} version changed"
            )
        content = stored["content"]
        if str(stored["sha256"]) != reference.sha256 or _stable_hash(
            content
        ) != reference.sha256:
            raise ContextReferenceError(
                f"Artifact {reference.artifact_id} failed integrity validation"
            )
        record["data"] = content
        record["artifact"] = reference.model_dump(mode="json")
        record["content_hash"] = reference.sha256
        return record

    def _pack_items(
        self,
        values: dict[str, object],
        partition: str,
        items: list[dict[str, object]],
        input_limit: int,
    ) -> tuple[list[dict[str, object]], list[str]]:
        kept: list[dict[str, object]] = []
        omitted: list[str] = []
        # Newest data wins, but the final Context remains chronological for the model.
        for item in reversed(items):
            candidate = [item, *kept]
            values[partition] = candidate
            if self._count(values) <= input_limit:
                kept = candidate
            elif "id" in item:
                omitted.append(str(item["id"]))
        values[partition] = kept
        return kept, omitted

    def _count(self, value: object) -> int:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return self.token_counter.count_text(serialized)

    def _remaining_call_budget(
        self, budget: RunBudget, usage: BudgetUsage
    ) -> tuple[int, int]:
        """Intersect Context limits with the Run's remaining input/output budget.

        This is reservation, not prediction: actual provider usage is still reconciled
        after the call. It prevents a late Run from advertising more output space or
        compiling more input than the deterministic ledger can possibly accept.
        """

        remaining_input = budget.max_input_tokens - usage.input_tokens
        remaining_output = budget.max_output_tokens - usage.output_tokens
        remaining_total = budget.max_total_tokens - usage.total_tokens
        output_reserve = min(
            self.reserved_output_tokens, remaining_output, remaining_total - 1
        )
        if output_reserve < 128:
            raise ContextBudgetError("remaining output token budget is below 128")
        input_capacity = min(
            self.max_context_tokens - output_reserve,
            remaining_input,
            remaining_total - output_reserve,
        )
        if input_capacity < 1:
            raise ContextBudgetError("no input token budget remains")
        return input_capacity + output_reserve, output_reserve


# Compatibility alias: callers keep the Stage 11 constructor name while receiving
# the complete Stage 12 implementation.
MinimalContextCompiler = ContextCompiler


_MANDATORY_PARTITIONS = {
    "policy",
    "objective",
    "plan_version",
    "current_step",
    "unresolved_items",
    "budget",
    "usage",
    "available_tools",
    "evidence",
    "applied_skill",
}
_PARTITION_PRIORITIES = {
    "policy": 100,
    "objective": 100,
    "plan_version": 100,
    "current_step": 100,
    "unresolved_items": 100,
    "budget": 100,
    "usage": 100,
    "available_tools": 100,
    "evidence": 95,
    "applied_skill": 96,
    "memory": 88,
    "recent_observations": 80,
    "completed_steps": 60,
    "conflicts": 90,
}


def _compact_tool(tool: ToolSpec) -> dict[str, object]:
    return {
        "name": tool.name,
        "description": tool.description,
        "risk": tool.risk,
        "approval_policy": tool.approval_policy,
        "read_only": tool.read_only,
        "input_schema": _strip_schema_noise(tool.input_schema),
    }


def _strip_schema_noise(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _strip_schema_noise(item)
            for key, item in value.items()
            if key not in {"title", "default", "examples"}
        }
    if isinstance(value, list):
        return [_strip_schema_noise(item) for item in value]
    return value


def _evidence_view(item: dict[str, object]) -> dict[str, object]:
    return {
        "id": item["id"],
        "plan_step_id": item["plan_step_id"],
        "source": item["source"],
        "succeeded": item["succeeded"],
        "summary": item["summary"],
        "data": item.get("data", {}),
        "artifact": item.get("artifact"),
        "created_at": item["created_at"],
    }


def _unresolved_items(state: DynamicAgentState) -> list[dict[str, object]]:
    items: list[dict[str, object]] = [
        {
            "step_id": item.id,
            "objective": item.objective,
            "status": item.status,
            "dependencies": item.dependencies,
            "success_criteria": item.success_criteria,
        }
        for item in state.plan.steps
        if item.status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}
    ]
    if state.pending_interrupt is not None:
        items.append(
            {
                "interrupt_id": state.pending_interrupt.id,
                "type": state.pending_interrupt.type,
                "step_id": state.pending_interrupt.plan_step_id,
            }
        )
    return items


def _deduplicate_observations(
    items: list[dict[str, object]],
    *,
    protected_ids: set[str],
) -> tuple[list[dict[str, object]], list[str]]:
    latest_by_signature: dict[str, dict[str, object]] = {}
    duplicates: list[str] = []
    for item in items:
        signature = _stable_hash(
            {
                "source": item.get("source"),
                "succeeded": item.get("succeeded"),
                "data": item.get("data"),
            }
        )
        previous = latest_by_signature.get(signature)
        if previous is not None:
            previous_id = str(previous["id"])
            current_id = str(item["id"])
            if previous_id in protected_ids:
                if current_id not in protected_ids:
                    duplicates.append(current_id)
                    continue
                # Two evidence ids remain separately addressable even if their payloads
                # match; compaction must never erase a Verifier reference.
                latest_by_signature[f"{signature}:{current_id}"] = item
                continue
            duplicates.append(previous_id)
        latest_by_signature[signature] = item
    return list(latest_by_signature.values()), duplicates


def _detect_conflicts(items: list[dict[str, object]]) -> list[ContextConflict]:
    values: dict[tuple[str, str], dict[str, list[str]]] = {}
    for item in items:
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        source = str(item.get("source", "unknown"))
        for key, value in data.items():
            fingerprint = _stable_hash(value)
            values.setdefault((source, str(key)), {}).setdefault(
                fingerprint, []
            ).append(str(item["id"]))
    return [
        ContextConflict(
            source=source,
            field=field,
            source_ids=[source_id for ids in variants.values() for source_id in ids],
        )
        for (source, field), variants in values.items()
        if len(variants) > 1
    ]


def _stable_hash(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(serialized.encode()).hexdigest()
