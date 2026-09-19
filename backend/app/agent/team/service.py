"""Bounded DAG scheduler and lifecycle service for general Agent Teams."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

from app.agent.delegation import DelegationResult
from app.agent.dynamic.models import DynamicAgentState
from app.agent.execution.store import SqliteAgentRunStore
from app.agent.policy import (
    AgentPolicyService,
    CapabilityGrant,
    PolicyAction,
    PolicyEffect,
    PolicyRequest,
    PolicySubject,
    attenuate_grant_resource,
)
from app.agent.team.models import (
    AgentCard,
    ArtifactDraft,
    TeamArtifact,
    TeamBudget,
    TeamFailurePolicy,
    TeamPart,
    TeamRunRequest,
    TeamRunResult,
    TeamTask,
    TeamTaskSpec,
    TeamTaskStatus,
)
from app.agent.team.registry import RoleAdapter, RoleRegistry
from app.agent.team.verifier import TeamArtifactVerifier


class TeamTaskCancelled(RuntimeError):
    pass


class AgentTeamService:
    """Execute a validated Task DAG with bounded fan-out and verified fan-in.

    Scheduler decisions are based only on the trusted manifest. Role adapters do
    not receive sibling Context, Lead Memory, Provider credentials, or authority
    beyond the explicit child grants recorded on their Task.
    """

    def __init__(
        self,
        *,
        store: SqliteAgentRunStore,
        registry: RoleRegistry,
        policy: AgentPolicyService,
        verifier: TeamArtifactVerifier | None = None,
        poll_seconds: float = 0.05,
        default_task_tokens: int = 6_000,
        deadline_seconds: float = 60,
        max_parallel_children: int = 2,
        max_children: int = 8,
        max_total_tokens: int = 12_000,
    ) -> None:
        self.store = store
        self.registry = registry
        self.policy = policy
        self.verifier = verifier or TeamArtifactVerifier()
        self.poll_seconds = poll_seconds
        self.default_task_tokens = default_task_tokens
        self.deadline_seconds = deadline_seconds
        self.max_parallel_children = max_parallel_children
        self.max_children = max_children
        self.max_total_tokens = max_total_tokens

    async def execute(self, request: TeamRunRequest) -> TeamRunResult:
        if request.budget.max_parallel_children > self.max_parallel_children:
            raise ValueError("Team request exceeds configured parallelism")
        if request.budget.max_children > self.max_children:
            raise ValueError("Team request exceeds configured child count")
        if request.budget.max_total_tokens > self.max_total_tokens:
            raise ValueError("Team request exceeds configured Token budget")
        if request.budget.deadline_seconds > self.deadline_seconds:
            raise ValueError("Team request exceeds configured deadline")
        parent = await self.store.get(request.parent_run_id)
        if parent is None or parent.status != "running" or parent.cancel_requested:
            raise ValueError("Lead Agent is not available for Team execution")
        await self._validate_parent_budget(request)
        deadline = (
            asyncio.get_running_loop().time() + request.budget.deadline_seconds
        )
        tasks = [await self._submit(request, spec) for spec in request.tasks]
        by_key = {item.task_key: item for item in tasks}
        artifacts: list[TeamArtifact] = []
        artifacts_by_key: dict[str, TeamArtifact] = {}
        pending = set(by_key)

        while pending:
            parent = await self.store.get(request.parent_run_id)
            if parent is None or parent.cancel_requested:
                await self._cancel_pending(by_key, pending, "parent_cancelled")
                break
            if asyncio.get_running_loop().time() >= deadline:
                await self._cancel_pending(by_key, pending, "team_deadline")
                break
            failed_keys = {
                key
                for key, task in by_key.items()
                if task.status in {TeamTaskStatus.FAILED, TeamTaskStatus.CANCELLED}
            }
            blocked = [
                key
                for key in pending
                if set(by_key[key].dependency_keys) & failed_keys
            ]
            for key in blocked:
                by_key[key] = await self._set_terminal(
                    by_key[key], TeamTaskStatus.CANCELLED, "dependency_failed"
                )
                pending.remove(key)
            if request.failure_policy == TeamFailurePolicy.FAIL_FAST and failed_keys:
                await self._cancel_pending(by_key, pending, "fail_fast")
                break

            completed_keys = {
                key
                for key, task in by_key.items()
                if task.status == TeamTaskStatus.COMPLETED
            }
            ready = [
                key
                for key in pending
                if set(by_key[key].dependency_keys).issubset(completed_keys)
            ]
            if not ready:
                if pending:
                    await self._cancel_pending(
                        by_key, pending, "unresolvable_dependencies"
                    )
                break
            semaphore = asyncio.Semaphore(request.budget.max_parallel_children)

            async def run_one(
                key: str, active_limit: asyncio.Semaphore = semaphore
            ) -> tuple[str, TeamTask, TeamArtifact | None]:
                async with active_limit:
                    dependency_artifacts = [
                        artifacts_by_key[dependency]
                        for dependency in by_key[key].dependency_keys
                        if dependency in artifacts_by_key
                    ]
                    prepared = self._attach_dependency_artifacts(
                        by_key[key], dependency_artifacts
                    )
                    if prepared is not by_key[key]:
                        by_key[key] = prepared
                        await self.store.update_team_task(prepared)
                    task, artifact = await self._execute_task(
                        by_key[key], deadline=deadline
                    )
                    return key, task, artifact

            # A wave may contain more Tasks than the concurrency limit; Semaphore
            # bounds active adapters while gather preserves deterministic fan-in.
            results = await asyncio.gather(*(run_one(key) for key in sorted(ready)))
            for key, task, artifact in results:
                by_key[key] = task
                pending.remove(key)
                if artifact is not None:
                    artifacts.append(artifact)
                    artifacts_by_key[key] = artifact

        ordered = [by_key[item.task_key] for item in request.tasks]
        result = TeamRunResult(
            parent_run_id=request.parent_run_id,
            tasks=ordered,
            artifacts=artifacts,
            failure_policy=request.failure_policy,
            completed=sum(item.status == TeamTaskStatus.COMPLETED for item in ordered),
            failed=sum(item.status == TeamTaskStatus.FAILED for item in ordered),
            cancelled=sum(item.status == TeamTaskStatus.CANCELLED for item in ordered),
            used_tokens=sum(item.used_tokens for item in ordered),
        )
        await self.store.append_event(
            request.parent_run_id,
            "team_completed",
            node=request.plan_step_id,
            data={
                "completed": result.completed,
                "failed": result.failed,
                "cancelled": result.cancelled,
                "used_tokens": result.used_tokens,
                "failure_policy": result.failure_policy,
            },
        )
        return result

    async def delegate_research(
        self, *, parent_run_id: str, plan_step_id: str, objective: str
    ) -> DelegationResult:
        """Compatibility facade used by the existing ``delegate.research`` Tool."""

        result = await self.execute(
            TeamRunRequest(
                parent_run_id=parent_run_id,
                plan_step_id=plan_step_id,
                tasks=[
                    TeamTaskSpec(
                        task_key="research",
                        role_id="researcher",
                        objective=objective,
                        allocated_tokens=self.default_task_tokens,
                    )
                ],
                budget=TeamBudget(
                    max_total_tokens=self.default_task_tokens,
                    deadline_seconds=self.deadline_seconds,
                    max_parallel_children=1,
                    max_children=1,
                ),
            )
        )
        if not result.artifacts:
            task = result.tasks[0]
            raise ValueError(task.error_code or "Researcher Team task failed")
        value = result.artifacts[0].parts[0].value
        return DelegationResult.model_validate(value)

    async def list_tasks(
        self, *, parent_run_id: str | None = None, limit: int = 200
    ) -> list[TeamTask]:
        return [
            TeamTask.model_validate_json(item)
            for item in await self.store.list_team_tasks(
                parent_run_id=parent_run_id, limit=limit
            )
        ]

    async def list_artifacts(
        self, *, parent_run_id: str | None = None, limit: int = 200
    ) -> list[TeamArtifact]:
        return [
            TeamArtifact.model_validate_json(item)
            for item in await self.store.list_team_artifacts(
                parent_run_id=parent_run_id, limit=limit
            )
        ]

    async def _submit(
        self, request: TeamRunRequest, spec: TeamTaskSpec
    ) -> TeamTask:
        if self.registry.get(spec.role_id) is None:
            raise ValueError(f"Team Role '{spec.role_id}' is not registered")
        fingerprint = _fingerprint(request, spec)
        existing = await self.store.find_team_task(
            request.parent_run_id, fingerprint
        )
        if existing is not None:
            return TeamTask.model_validate_json(existing)
        task = TeamTask(
            parent_run_id=request.parent_run_id,
            plan_step_id=request.plan_step_id,
            task_key=spec.task_key,
            role_id=spec.role_id,
            objective=spec.objective,
            parts=spec.parts,
            dependency_keys=spec.dependency_keys,
            allocated_tokens=spec.allocated_tokens,
            fingerprint=fingerprint,
        )
        created = await self.store.create_team_task(task)
        if not created:
            raced = await self.store.find_team_task(
                request.parent_run_id, fingerprint
            )
            if raced is None:
                raise RuntimeError("Team task disappeared after insert race")
            return TeamTask.model_validate_json(raced)
        await self.store.append_event(
            request.parent_run_id,
            "team_task_submitted",
            node=spec.role_id,
            data={
                "task_id": task.id,
                "task_key": task.task_key,
                "role_id": task.role_id,
                "dependency_keys": task.dependency_keys,
                "allocated_tokens": task.allocated_tokens,
            },
        )
        return task

    async def _validate_parent_budget(self, request: TeamRunRequest) -> None:
        raw_state = await self.store.load_dynamic_state(request.parent_run_id)
        if raw_state is None:
            return
        state = DynamicAgentState.model_validate_json(raw_state)
        existing = await self.list_tasks(parent_run_id=request.parent_run_id)
        existing_fingerprints = {item.fingerprint for item in existing}
        existing_reservations = sum(item.allocated_tokens for item in existing)
        new_reservations = sum(
            item.allocated_tokens
            for item in request.tasks
            if _fingerprint(request, item) not in existing_fingerprints
        )
        remaining_tokens = state.budget.max_total_tokens - state.usage.total_tokens
        if existing_reservations + new_reservations > remaining_tokens:
            raise ValueError("Team reservations exceed the Lead Agent Token budget")
        elapsed = (datetime.now(UTC) - state.usage.started_at).total_seconds()
        remaining_deadline = state.budget.deadline_seconds - elapsed
        if request.budget.deadline_seconds > remaining_deadline:
            raise ValueError("Team deadline exceeds the Lead Agent deadline")

    async def _execute_task(
        self, task: TeamTask, *, deadline: float
    ) -> tuple[TeamTask, TeamArtifact | None]:
        if task.status == TeamTaskStatus.COMPLETED and task.artifact_id:
            raw = await self.store.get_team_artifact(task.artifact_id)
            artifact = TeamArtifact.model_validate_json(raw) if raw else None
            if artifact is not None and self.verifier.revalidate(artifact):
                return task, artifact
        adapter = self.registry.get(task.role_id)
        if adapter is None:
            return await self._set_terminal(
                task, TeamTaskStatus.FAILED, "role_unavailable"
            ), None
        try:
            task = await self._authorize(task, adapter.card)
            task = task.model_copy(
                update={
                    "status": TeamTaskStatus.RUNNING,
                    "started_at": datetime.now(UTC),
                }
            )
            await self.store.update_team_task(task)
            await self.store.append_event(
                task.parent_run_id,
                "team_task_started",
                node=task.role_id,
                data={"task_id": task.id, "role_id": task.role_id},
            )
            draft = await self._invoke_adapter(task, adapter, deadline)
            artifact = self.verifier.verify(task, adapter.card, draft)
            artifact = await self._authorize_artifact(task, artifact)
            await self.store.save_team_artifact(task.parent_run_id, artifact)
            task = task.model_copy(
                update={
                    "status": TeamTaskStatus.COMPLETED,
                    "artifact_id": artifact.id,
                    "used_tokens": draft.used_tokens,
                    "completed_at": datetime.now(UTC),
                }
            )
            await self.store.update_team_task(task)
            await self.store.append_event(
                task.parent_run_id,
                "team_artifact_verified",
                node=task.role_id,
                data={
                    "task_id": task.id,
                    "artifact_id": artifact.id,
                    "sha256": artifact.sha256,
                    "label_ids": [item.id for item in artifact.labels],
                    "used_tokens": task.used_tokens,
                },
            )
            return task, artifact
        except TeamTaskCancelled as error:
            return await self._set_terminal(
                task, TeamTaskStatus.CANCELLED, str(error)
            ), None
        except Exception as error:
            return await self._set_terminal(
                task,
                TeamTaskStatus.FAILED,
                f"{type(error).__name__}:{str(error)[:300]}",
            ), None

    async def _authorize(self, task: TeamTask, card: AgentCard) -> TeamTask:
        parent_subject = PolicySubject(
            id=f"run:{task.parent_run_id}:lead",
            kind="lead_agent",
            run_id=task.parent_run_id,
        )
        resource = (
            f"run:{task.parent_run_id}:step:{task.plan_step_id}:"
            f"team:{task.id}:role:{task.role_id}"
        )
        decision = await self.policy.decide(
            PolicyRequest(
                subject=parent_subject,
                action=PolicyAction.DELEGATE,
                capability=f"agent:delegate:{task.role_id}",
                resource=resource,
                risk=card.risk,
                read_only=card.read_only,
                labels=[label for part in task.parts for label in part.labels],
                grants=[
                    CapabilityGrant(
                        id=f"team-delegate:{task.id}",
                        subject_id=parent_subject.id,
                        capability=f"agent:delegate:{task.role_id}",
                        resource_pattern=resource,
                        source=f"team-manifest:{task.plan_step_id}",
                        max_delegation_depth=1,
                    )
                ],
                request_ref=f"team-task:{task.id}",
            )
        )
        if decision.effect != PolicyEffect.ALLOW:
            raise ValueError(f"Team delegation denied: {decision.reason.value}")
        child_subject = f"run:{task.parent_run_id}:team-task:{task.id}"
        child_grants: list[CapabilityGrant] = []
        for tool_name in card.allowed_tools:
            parent_pattern = f"run:{task.parent_run_id}:team-task:*"
            child_resource = (
                f"run:{task.parent_run_id}:team-task:{task.id}:tool:{tool_name}"
            )
            if not attenuate_grant_resource(parent_pattern, child_resource):
                raise ValueError("Child Tool Grant escaped parent resource scope")
            child_grants.append(
                CapabilityGrant(
                    id=f"team-tool:{task.id}:{tool_name}",
                    subject_id=child_subject,
                    capability=f"tool:{tool_name}",
                    resource_pattern=child_resource,
                    source=f"team-role:{card.role_id}@{card.version}",
                    max_delegation_depth=1,
                )
            )
        updated = task.model_copy(
            update={
                "policy_decision_id": decision.id,
                "child_grants": child_grants,
            }
        )
        await self.store.update_team_task(updated)
        return updated

    def _attach_dependency_artifacts(
        self, task: TeamTask, artifacts: list[TeamArtifact]
    ) -> TeamTask:
        existing_ids = {
            str(part.value.get("artifact_id"))
            for part in task.parts
            if part.kind == "evidence_ref" and isinstance(part.value, dict)
        }
        additions = [
            TeamPart(
                kind="evidence_ref",
                value={
                    "artifact_id": artifact.id,
                    "sha256": artifact.sha256,
                    "role_id": artifact.role_id,
                },
                labels=artifact.labels,
            )
            for artifact in artifacts
            if artifact.id not in existing_ids
        ]
        if not additions:
            return task
        return task.model_copy(update={"parts": [*task.parts, *additions]})

    async def _authorize_artifact(
        self, task: TeamTask, artifact: TeamArtifact
    ) -> TeamArtifact:
        subject_id = f"run:{task.parent_run_id}:lead"
        resource = (
            f"run:{task.parent_run_id}:team-task:{task.id}:"
            f"artifact:{artifact.id}"
        )
        decision = await self.policy.decide(
            PolicyRequest(
                subject=PolicySubject(
                    id=subject_id,
                    kind="lead_agent",
                    run_id=task.parent_run_id,
                ),
                action=PolicyAction.ARTIFACT_IMPORT,
                capability=f"artifact:import:{task.role_id}",
                resource=resource,
                read_only=True,
                labels=artifact.labels,
                grants=[
                    CapabilityGrant(
                        id=f"team-artifact:{artifact.id}",
                        subject_id=subject_id,
                        capability=f"artifact:import:{task.role_id}",
                        resource_pattern=resource,
                        source=f"team-verifier:{self.verifier.version}",
                    )
                ],
                request_ref=f"team-artifact:{artifact.id}",
            )
        )
        if decision.effect != PolicyEffect.ALLOW:
            raise ValueError(f"Team Artifact denied: {decision.reason.value}")
        return artifact.model_copy(update={"policy_decision_id": decision.id})

    async def _invoke_adapter(
        self, task: TeamTask, adapter: RoleAdapter, deadline: float
    ) -> ArtifactDraft:
        invocation = asyncio.create_task(adapter.execute(task))
        try:
            while not invocation.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TeamTaskCancelled("team_deadline")
                await asyncio.wait(
                    {invocation}, timeout=min(self.poll_seconds, remaining)
                )
                parent = await self.store.get(task.parent_run_id)
                if parent is None or parent.cancel_requested:
                    raise TeamTaskCancelled("parent_cancelled")
            return await invocation
        except BaseException:
            invocation.cancel()
            await asyncio.gather(invocation, return_exceptions=True)
            raise

    async def _set_terminal(
        self, task: TeamTask, status: TeamTaskStatus, error_code: str
    ) -> TeamTask:
        updated = task.model_copy(
            update={
                "status": status,
                "error_code": error_code,
                "completed_at": datetime.now(UTC),
            }
        )
        await self.store.update_team_task(updated)
        await self.store.append_event(
            task.parent_run_id,
            "team_task_cancelled"
            if status == TeamTaskStatus.CANCELLED
            else "team_task_failed",
            node=task.role_id,
            data={
                "task_id": task.id,
                "role_id": task.role_id,
                "status": status,
                "error_code": error_code,
            },
        )
        return updated

    async def _cancel_pending(
        self,
        tasks: dict[str, TeamTask],
        pending: set[str],
        reason: str,
    ) -> None:
        for key in sorted(pending):
            tasks[key] = await self._set_terminal(
                tasks[key], TeamTaskStatus.CANCELLED, reason
            )
        pending.clear()


def _fingerprint(request: TeamRunRequest, spec: TeamTaskSpec) -> str:
    canonical = {
        "parent_run_id": request.parent_run_id,
        "plan_step_id": request.plan_step_id,
        "task_key": spec.task_key,
        "role_id": spec.role_id,
        "objective": spec.objective,
        "parts": [item.model_dump(mode="json") for item in spec.parts],
        "dependencies": sorted(spec.dependency_keys),
        "allocated_tokens": spec.allocated_tokens,
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
