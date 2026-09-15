"""Deterministic Reflection pipeline and governed Skill Library."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from app.agent.dynamic.models import AgentPlan, DynamicAgentState
from app.agent.execution.models import AgentEvent
from app.agent.execution.store import SqliteAgentRunStore
from app.agent.experience.models import (
    EvidenceKind,
    ReflectionEvidence,
    ReflectionInsight,
    ReflectionOutcome,
    RunReflection,
    SkillApplicability,
    SkillRecall,
    SkillRecord,
    SkillReviewRequest,
    SkillRevisionRequest,
    SkillRisk,
    SkillStatus,
    SkillStatusRequest,
    SkillStep,
    SkillUsage,
)


class ReflectionSkillService:
    """Turn verified traces into reviewed, bounded procedural guidance.

    The service is intentionally deterministic. It never asks a model to invent a
    root cause: every Reflection insight points to an immutable public Trace event,
    and candidate Skills are synthesized only when multiple successful Runs share
    the same normalized strategy. Human review is the promotion boundary.
    """

    def __init__(
        self,
        store: SqliteAgentRunStore,
        *,
        enabled: bool = True,
        minimum_source_runs: int = 2,
        quarantine_min_uses: int = 3,
        quarantine_success_rate: float = 0.5,
        recall_limit: int = 3,
    ) -> None:
        if minimum_source_runs < 2:
            raise ValueError("a candidate Skill requires at least two source Runs")
        if quarantine_min_uses < 1:
            raise ValueError("quarantine_min_uses must be positive")
        if not 0 <= quarantine_success_rate <= 1:
            raise ValueError("quarantine_success_rate must be between 0 and 1")
        if not 1 <= recall_limit <= 10:
            raise ValueError("recall_limit must be between 1 and 10")
        self.store = store
        self.enabled = enabled
        self.minimum_source_runs = minimum_source_runs
        self.quarantine_min_uses = quarantine_min_uses
        self.quarantine_success_rate = quarantine_success_rate
        self.recall_limit = recall_limit

    async def process_run(self, run_id: str) -> list[AgentEvent]:
        """Idempotently reflect on a terminal Run and maybe extract a candidate.

        A cancelled Run or a Run without deterministic Verifier events is ignored.
        That fail-closed gate prevents user prose, retrieved documents, and mere
        model confidence from being promoted into the experience system.
        """

        if not self.enabled:
            return []
        existing = await self.store.get_reflection_for_run(run_id)
        if existing is not None:
            return []
        run = await self.store.get(run_id)
        if run is None:
            raise LookupError(f"agent run {run_id} was not found")
        if run.status not in {"completed", "failed"}:
            return []
        raw_state = await self.store.load_dynamic_state(run_id)
        if raw_state is None:
            return []
        state = DynamicAgentState.model_validate_json(raw_state)
        events = await self.store.list_events(run_id)
        verifier_events = [
            item for item in events if item.event == "verification_completed"
        ]
        if not verifier_events:
            return []

        reflection = self._build_reflection(run.graph_kind, state, events)
        created = await self.store.save_reflection(
            reflection_id=reflection.id,
            run_id=run_id,
            outcome=reflection.outcome,
            strategy_key=reflection.strategy_key,
            reflection_json=reflection.model_dump_json(),
            created_at=reflection.created_at,
        )
        if not created:
            return []
        emitted = [
            await self.store.append_event(
                run_id,
                "reflection_created",
                data={
                    "reflection_id": reflection.id,
                    "outcome": reflection.outcome,
                    "problem_category": reflection.problem_category,
                    "evidence_count": len(reflection.evidence),
                },
            )
        ]
        candidate = await self._extract_candidate(reflection, state)
        if candidate is not None:
            emitted.append(
                await self.store.append_event(
                    run_id,
                    "skill_candidate_created",
                    data={
                        "skill_id": candidate.id,
                        "version": candidate.version,
                        "source_run_ids": candidate.source_run_ids,
                        "status": candidate.status,
                    },
                )
            )
        return emitted

    def _build_reflection(
        self, graph_kind: str, state: DynamicAgentState, events: list[AgentEvent]
    ) -> RunReflection:
        evidence: list[ReflectionEvidence] = []
        for event in events:
            if event.event not in {
                "observation_recorded",
                "verification_completed",
                "run_completed",
                "run_failed",
            }:
                continue
            kind = (
                EvidenceKind.OBSERVATION
                if event.event == "observation_recorded"
                else EvidenceKind.VERIFICATION
                if event.event == "verification_completed"
                else EvidenceKind.TERMINAL
            )
            summary = _event_summary(event)
            evidence.append(
                ReflectionEvidence(
                    kind=kind,
                    event_sequence=event.sequence,
                    observation_id=(
                        str(event.data.get("id"))
                        if event.event == "observation_recorded"
                        and event.data.get("id")
                        else None
                    ),
                    summary=summary,
                    content_sha256=_hash(
                        {
                            "event": event.event,
                            "sequence": event.sequence,
                            "data": event.data,
                        }
                    ),
                )
            )
        evidence_by_sequence = {item.event_sequence: item.id for item in evidence}
        failed_observations = [
            item
            for item in events
            if item.event == "observation_recorded"
            and item.data.get("succeeded") is False
        ]
        failed_verifiers = [
            item
            for item in events
            if item.event == "verification_completed"
            and str(item.data.get("status")) != "passed"
        ]
        terminal = next(
            (
                item
                for item in reversed(events)
                if item.event in {"run_completed", "run_failed"}
            ),
            None,
        )
        outcome = (
            ReflectionOutcome.SUCCESS
            if terminal is not None and terminal.event == "run_completed"
            else ReflectionOutcome.FAILURE
        )
        root_causes: list[ReflectionInsight] = []
        improvements: list[ReflectionInsight] = []
        if outcome == ReflectionOutcome.FAILURE:
            for item in [*failed_observations, *failed_verifiers]:
                evidence_id = evidence_by_sequence.get(item.sequence)
                if evidence_id is None:
                    continue
                statement = (
                    f"Observed execution failure: {_event_summary(item)}"
                    if item.event == "observation_recorded"
                    else f"Verifier did not pass: {_event_summary(item)}"
                )
                root_causes.append(
                    ReflectionInsight(statement=statement, evidence_ids=[evidence_id])
                )
                improvements.append(
                    ReflectionInsight(
                        statement=(
                            "Before repeating this strategy, address the cited "
                            "failure and rerun its deterministic verifier."
                        ),
                        evidence_ids=[evidence_id],
                    )
                )
        else:
            passed = [
                item
                for item in events
                if item.event == "verification_completed"
                and str(item.data.get("status")) == "passed"
            ]
            cited = [evidence_by_sequence[item.sequence] for item in passed]
            if cited:
                improvements.append(
                    ReflectionInsight(
                        statement=(
                            "The completed Step sequence may be reused only under "
                            "equivalent applicability and verifier constraints."
                        ),
                        evidence_ids=cited,
                    )
                )
        strategy_key = _strategy_key(graph_kind, state.plan)
        return RunReflection(
            run_id=state.run_id,
            outcome=outcome,
            problem_category=_problem_category(events),
            evidence=evidence,
            root_causes=root_causes,
            improvements=improvements,
            applicability=[graph_kind, *_objective_keywords(state.plan.objective)],
            strategy_key=strategy_key,
        )

    async def _extract_candidate(
        self, reflection: RunReflection, state: DynamicAgentState
    ) -> SkillRecord | None:
        if reflection.outcome != ReflectionOutcome.SUCCESS:
            return None
        raw = await self.store.list_reflections(
            strategy_key=reflection.strategy_key,
            outcome=ReflectionOutcome.SUCCESS,
            limit=100,
        )
        reflections = [RunReflection.model_validate_json(item) for item in raw]
        source_reflections = reflections[:20]
        source_run_ids = sorted({item.run_id for item in source_reflections})
        if len(source_run_ids) < self.minimum_source_runs:
            return None
        existing = [
            SkillRecord.model_validate_json(item)
            for item in await self.store.list_skills(limit=500)
        ]
        if any(item.family_key == reflection.strategy_key for item in existing):
            return None
        tools = sorted(
            {tool for step in state.plan.steps for tool in step.allowed_tools}
        )
        keyword_sets = [set(item.applicability[1:]) for item in reflections]
        common_keywords = set.intersection(*keyword_sets) if keyword_sets else set()
        if not common_keywords:
            common_keywords = set(_objective_keywords(state.plan.objective))
        candidate = SkillRecord(
            family_key=reflection.strategy_key,
            name=f"Verified procedure: {state.plan.objective[:120]}",
            description=(
                "A candidate procedure extracted from repeated successful Runs. "
                "It remains inert until human review publishes this exact version."
            ),
            risk=(
                SkillRisk.MEDIUM
                if any(_is_write_tool(tool) for tool in tools)
                else SkillRisk.LOW
            ),
            applicability=SkillApplicability(
                graph_kind="daily_learning",
                objective_keywords=sorted(common_keywords)[:20],
                required_tools=tools,
            ),
            prerequisites=["Current Run has the listed Tool allowlist and budget"],
            steps=[
                SkillStep(
                    order=index,
                    instruction=step.objective,
                    allowed_tools=step.allowed_tools,
                    verifier="; ".join(step.success_criteria),
                )
                for index, step in enumerate(state.plan.steps, start=1)
            ],
            source_run_ids=source_run_ids,
            source_reflections=source_reflections,
        )
        created = await self.store.create_skill(
            skill_id=candidate.id,
            family_key=candidate.family_key,
            version=candidate.version,
            status=candidate.status,
            skill_json=candidate.model_dump_json(),
            created_at=candidate.created_at,
            updated_at=candidate.updated_at,
        )
        return candidate if created else None

    async def list_reflections(
        self, *, run_id: str | None = None, limit: int = 100
    ) -> list[RunReflection]:
        return [
            RunReflection.model_validate_json(item)
            for item in await self.store.list_reflections(run_id=run_id, limit=limit)
        ]

    async def recall_failures(
        self, *, graph_kind: str, objective: str, limit: int = 3
    ) -> list[RunReflection]:
        """Return only evidence-bound failure experience matching this objective."""

        if not self.enabled:
            return []
        matched: list[RunReflection] = []
        for reflection in await self.list_reflections(limit=200):
            if reflection.outcome != ReflectionOutcome.FAILURE:
                continue
            if (
                not reflection.applicability
                or reflection.applicability[0] != graph_kind
            ):
                continue
            keywords = reflection.applicability[1:]
            if any(keyword.casefold() in objective.casefold() for keyword in keywords):
                matched.append(reflection)
        return matched[:limit]

    async def list_skills(
        self, *, status: SkillStatus | None = None, limit: int = 100
    ) -> list[SkillRecord]:
        return [
            SkillRecord.model_validate_json(item)
            for item in await self.store.list_skills(
                status=status.value if status is not None else None, limit=limit
            )
        ]

    async def get_skill(self, skill_id: str) -> SkillRecord | None:
        raw = await self.store.get_skill(skill_id)
        return SkillRecord.model_validate_json(raw) if raw is not None else None

    async def review(self, skill_id: str, request: SkillReviewRequest) -> SkillRecord:
        skill = await self.get_skill(skill_id)
        if skill is None:
            raise LookupError(f"Skill {skill_id} was not found")
        target = (
            SkillStatus.ACTIVE
            if request.decision == "publish"
            else SkillStatus.REJECTED
        )
        updated = skill.model_copy(
            update={
                "status": target,
                "review_note": request.note,
                "updated_at": datetime.now(UTC),
            }
        )
        if target == SkillStatus.ACTIVE:
            await self._deactivate_active_family(
                skill.family_key, except_skill_id=skill.id
            )
        changed = await self.store.replace_skill(
            skill_id=skill.id,
            expected_version=request.expected_version,
            expected_statuses={SkillStatus.CANDIDATE},
            status=updated.status,
            skill_json=updated.model_dump_json(),
            updated_at=updated.updated_at,
        )
        if not changed:
            raise RuntimeError(
                "Skill review conflict or candidate is no longer reviewable"
            )
        return updated

    async def set_status(
        self, skill_id: str, request: SkillStatusRequest
    ) -> SkillRecord:
        skill = await self.get_skill(skill_id)
        if skill is None:
            raise LookupError(f"Skill {skill_id} was not found")
        allowed: dict[SkillStatus, set[SkillStatus]] = {
            SkillStatus.ACTIVE: {SkillStatus.DISABLED},
            SkillStatus.DISABLED: {SkillStatus.ACTIVE, SkillStatus.QUARANTINED},
            SkillStatus.QUARANTINED: {SkillStatus.ACTIVE},
            SkillStatus.CANDIDATE: {SkillStatus.QUARANTINED, SkillStatus.DISABLED},
        }
        if skill.status not in allowed.get(request.status, set()):
            raise RuntimeError(
                f"cannot transition Skill {skill.status} to {request.status}"
            )
        updated = skill.model_copy(
            update={
                "status": request.status,
                "review_note": request.note,
                "updated_at": datetime.now(UTC),
            }
        )
        if request.status == SkillStatus.ACTIVE:
            await self._deactivate_active_family(
                skill.family_key, except_skill_id=skill.id
            )
        changed = await self.store.replace_skill(
            skill_id=skill.id,
            expected_version=request.expected_version,
            expected_statuses={skill.status},
            status=updated.status,
            skill_json=updated.model_dump_json(),
            updated_at=updated.updated_at,
        )
        if not changed:
            raise RuntimeError("Skill lifecycle conflict")
        return updated

    async def revise(self, skill_id: str, request: SkillRevisionRequest) -> SkillRecord:
        """Create an immutable candidate version without replacing the live Skill."""

        skill = await self.get_skill(skill_id)
        if skill is None:
            raise LookupError(f"Skill {skill_id} was not found")
        if skill.version != request.expected_version:
            raise RuntimeError("Skill revision version conflict")
        family = [
            item
            for item in await self.list_skills(limit=500)
            if item.family_key == skill.family_key
        ]
        next_version = max(item.version for item in family) + 1
        now = datetime.now(UTC)
        revision = SkillRecord(
            family_key=skill.family_key,
            name=skill.name,
            description=request.description,
            version=next_version,
            status=SkillStatus.CANDIDATE,
            risk=request.risk,
            applicability=request.applicability,
            prerequisites=request.prerequisites,
            steps=request.steps,
            source_run_ids=skill.source_run_ids,
            source_reflections=skill.source_reflections,
            review_note=request.note,
            created_at=now,
            updated_at=now,
        )
        created = await self.store.create_skill(
            skill_id=revision.id,
            family_key=revision.family_key,
            version=revision.version,
            status=revision.status,
            skill_json=revision.model_dump_json(),
            created_at=revision.created_at,
            updated_at=revision.updated_at,
        )
        if not created:
            raise RuntimeError("Skill revision conflict")
        return revision

    async def _deactivate_active_family(
        self, family_key: str, *, except_skill_id: str
    ) -> None:
        for current in await self.list_skills(status=SkillStatus.ACTIVE, limit=500):
            if current.family_key != family_key or current.id == except_skill_id:
                continue
            updated = current.model_copy(
                update={
                    "status": SkillStatus.DISABLED,
                    "review_note": "Superseded by another active family version.",
                    "updated_at": datetime.now(UTC),
                }
            )
            await self.store.replace_skill(
                skill_id=current.id,
                expected_version=current.version,
                expected_statuses={SkillStatus.ACTIVE},
                status=updated.status,
                skill_json=updated.model_dump_json(),
                updated_at=updated.updated_at,
            )

    async def recall(
        self,
        *,
        graph_kind: str,
        objective: str,
        allowed_tools: set[str],
    ) -> list[SkillRecall]:
        """Recall only active, current, scope-compatible Skill versions.

        ``required_tools ⊆ allowed_tools`` is the capability non-escalation check.
        A Skill that mentions a Tool absent from this Run is excluded rather than
        silently weakening or expanding its procedure.
        """

        if not self.enabled:
            return []
        now = datetime.now(UTC)
        recalls: list[SkillRecall] = []
        for skill in await self.list_skills(status=SkillStatus.ACTIVE, limit=500):
            if skill.valid_until is not None and skill.valid_until <= now:
                continue
            if skill.applicability.graph_kind != graph_kind:
                continue
            if not set(skill.applicability.required_tools).issubset(allowed_tools):
                continue
            matched = [
                keyword
                for keyword in skill.applicability.objective_keywords
                if keyword.casefold() in objective.casefold()
            ]
            if not matched:
                continue
            keyword_score = len(matched) / len(skill.applicability.objective_keywords)
            reliability = skill.success_rate if skill.success_rate is not None else 0.5
            recalls.append(
                SkillRecall(
                    skill=skill,
                    matched_keywords=matched,
                    score=min(1.0, 0.75 * keyword_score + 0.25 * reliability),
                )
            )
        return sorted(recalls, key=lambda item: (-item.score, item.skill.id))[
            : self.recall_limit
        ]

    async def validate_and_start_usage(
        self, run_id: str, plan: AgentPlan, recalls: list[SkillRecall]
    ) -> None:
        if plan.applied_skill_id is None:
            return
        selected = next(
            (
                item.skill
                for item in recalls
                if item.skill.id == plan.applied_skill_id
                and item.skill.version == plan.applied_skill_version
            ),
            None,
        )
        if selected is None:
            raise ValueError(
                "Plan selected a Skill that was not recalled for this scope"
            )
        usage = SkillUsage(
            run_id=run_id,
            skill_id=selected.id,
            skill_version=selected.version,
            status="running",
        )
        await self.store.save_skill_usage(
            run_id=run_id,
            skill_id=selected.id,
            skill_version=selected.version,
            usage_json=usage.model_dump_json(),
        )

    async def complete_usage(self, run_id: str) -> list[AgentEvent]:
        raw = await self.store.get_skill_usage(run_id)
        if raw is None:
            return []
        usage = SkillUsage.model_validate_json(raw)
        if usage.status == "completed":
            return []
        run = await self.store.get(run_id)
        state_raw = await self.store.load_dynamic_state(run_id)
        if (
            run is None
            or state_raw is None
            or run.status not in {"completed", "failed"}
        ):
            return []
        state = DynamicAgentState.model_validate_json(state_raw)
        completed = usage.model_copy(
            update={
                "status": "completed",
                "succeeded": run.status == "completed",
                "tool_calls": state.usage.tool_calls,
                "tokens": state.usage.total_tokens,
                "failure_type": None
                if run.status == "completed"
                else run.terminal_reason,
                "completed_at": datetime.now(UTC),
            }
        )
        changed = await self.store.finish_skill_usage(
            run_id=run_id,
            usage_json=completed.model_dump_json(),
            completed_at=completed.completed_at or datetime.now(UTC),
        )
        if not changed:
            return []
        events = [
            await self.store.append_event(
                run_id,
                "skill_usage_recorded",
                data=completed.model_dump(mode="json"),
            )
        ]
        quarantined = await self._refresh_skill_statistics(completed.skill_id)
        if quarantined:
            events.append(
                await self.store.append_event(
                    run_id,
                    "skill_quarantined",
                    data={
                        "skill_id": completed.skill_id,
                        "reason": "success_rate_below_threshold",
                    },
                )
            )
        return events

    async def _refresh_skill_statistics(self, skill_id: str) -> bool:
        skill = await self.get_skill(skill_id)
        if skill is None:
            return False
        usages = [
            SkillUsage.model_validate_json(item)
            for item in await self.store.list_skill_usages(skill_id)
        ]
        success_count = sum(item.succeeded is True for item in usages)
        failure_count = sum(item.succeeded is False for item in usages)
        total = success_count + failure_count
        status = skill.status
        quarantined = (
            status == SkillStatus.ACTIVE
            and total >= self.quarantine_min_uses
            and success_count / total < self.quarantine_success_rate
        )
        if quarantined:
            status = SkillStatus.QUARANTINED
        updated = skill.model_copy(
            update={
                "status": status,
                "success_count": success_count,
                "failure_count": failure_count,
                "average_tool_calls": sum(item.tool_calls for item in usages) / total,
                "average_tokens": sum(item.tokens for item in usages) / total,
                "updated_at": datetime.now(UTC),
            }
        )
        await self.store.replace_skill(
            skill_id=skill.id,
            expected_version=skill.version,
            expected_statuses={skill.status},
            status=updated.status,
            skill_json=updated.model_dump_json(),
            updated_at=updated.updated_at,
        )
        return quarantined


def _strategy_key(graph_kind: str, plan: AgentPlan) -> str:
    return _hash(
        {
            "graph_kind": graph_kind,
            "steps": [
                {
                    "tools": sorted(step.allowed_tools),
                    "criteria": sorted(
                        item.casefold() for item in step.success_criteria
                    ),
                    "dependencies": sorted(step.dependencies),
                }
                for step in plan.steps
            ],
        }
    )


def _objective_keywords(objective: str) -> list[str]:
    tail = re.split(r"[:：]", objective)[-1]
    ascii_words = re.findall(r"[A-Za-z0-9_+-]{2,}", tail.casefold())
    chinese = re.findall(r"[\u4e00-\u9fff]{2,8}", tail)
    tokens = list(dict.fromkeys([*ascii_words, *chinese]))
    return tokens[:20] or [objective[:20].casefold()]


def _problem_category(events: list[AgentEvent]) -> str:
    if any(item.event == "delegation_started" for item in events):
        return "delegated_research"
    if any(item.node == "research.ask" for item in events):
        return "agentic_research"
    if any(item.event == "run_failed" for item in events):
        return "execution_failure"
    return "learning_session"


def _event_summary(event: AgentEvent) -> str:
    if event.event == "observation_recorded":
        return str(event.data.get("summary", "Observation recorded"))[:2_000]
    if event.event == "verification_completed":
        status = event.data.get("status", "unknown")
        explanation = event.data.get("explanation", "")
        return f"Verifier {status}: {explanation}"[:2_000]
    return str(event.data.get("message") or event.data.get("summary") or event.event)[
        :2_000
    ]


def _is_write_tool(name: str) -> bool:
    return name in {"exercise.grade", "review.schedule", "session.complete"}


def _hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
