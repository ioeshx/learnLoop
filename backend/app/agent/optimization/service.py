"""Governed policy optimization, delayed rewards, and offline evaluation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from datetime import UTC, datetime
from typing import cast

from app.agent.dynamic.models import DynamicAgentState
from app.agent.execution.models import AgentEvent
from app.agent.execution.store import SqliteAgentRunStore
from app.agent.experience import RunReflection
from app.agent.optimization.models import (
    AgentTransition,
    BanditDecision,
    DelayedLearningOutcome,
    ExperimentManifest,
    ExperimentReport,
    ExperimentStatus,
    FailureCluster,
    PolicyActivationRequest,
    PolicyRevisionRequest,
    PolicyStatus,
    PolicyVersion,
    PreferencePair,
    ReplaySample,
    RewardComponents,
    RewardEvidence,
    RewardRecord,
    RewardStatus,
    SFTTrajectory,
    TeachingArm,
    TeachingContext,
    TrajectoryReview,
)

_FEATURE_DIMENSION = 5


class PolicyOptimizationService:
    """Stage 17 control plane for learning from verified Agent trajectories.

    Online policy selection is deliberately limited to pedagogical guidance. Tool
    permissions, Agent budgets, Verifiers, and safety policy remain outside the
    learned policy. Rewards mature only after retention and transfer labels arrive;
    safety violations make a sample permanently ineligible for optimization.
    """

    def __init__(
        self,
        store: SqliteAgentRunStore,
        *,
        enabled: bool = False,
        expected_latency_ms: float = 30_000,
    ) -> None:
        if expected_latency_ms <= 0:
            raise ValueError("expected_latency_ms must be positive")
        self.store = store
        self.enabled = enabled
        self.expected_latency_ms = expected_latency_ms
        self._update_lock = asyncio.Lock()

    async def ensure_default_policy(self) -> PolicyVersion:
        policies = await self.list_policies()
        if policies:
            # version 1 是代码内置的 Bootstrap Policy，不是训练产物。首次从
            # Shadow 切到 enabled 时允许直接启用；后续 Candidate 仍必须通过
            # frozen holdout experiment，不能借此绕过 Promotion Gate。
            active = [item for item in policies if item.status == PolicyStatus.ACTIVE]
            bootstrap = next(
                (
                    item
                    for item in policies
                    if item.version == 1
                    and item.source_experiment_id is None
                    and item.status == PolicyStatus.CANDIDATE
                ),
                None,
            )
            if self.enabled and not active and bootstrap is not None:
                enabled = bootstrap.model_copy(update={"status": PolicyStatus.ACTIVE})
                changed = await self.store.replace_policy_version(
                    policy_id=bootstrap.id,
                    expected_status=bootstrap.status,
                    expected_version=bootstrap.version,
                    status=enabled.status,
                    policy_json=enabled.model_dump_json(),
                )
                if changed:
                    return enabled
            return policies[0]
        policy = PolicyVersion(
            status=PolicyStatus.ACTIVE if self.enabled else PolicyStatus.CANDIDATE,
            arms=[
                TeachingArm(
                    id="direct_explanation",
                    instruction=(
                        "Give a concise direct explanation, then require a verified "
                        "learner action before completion."
                    ),
                    prohibited_actions=["bypass_verifier", "reveal_hidden_reasoning"],
                ),
                TeachingArm(
                    id="socratic_prompt",
                    instruction=(
                        "Use one bounded Socratic prompt and request learner input; "
                        "do not mark mastery without grading."
                    ),
                    prohibited_actions=["claim_unverified_mastery", "reveal_answer"],
                ),
                TeachingArm(
                    id="worked_example",
                    instruction=(
                        "After an observed failure, present one worked example and "
                        "verify transfer with a different exercise."
                    ),
                    prohibited_actions=["repeat_same_failed_action"],
                ),
                TeachingArm(
                    id="retrieval_grounded",
                    instruction=(
                        "Use accepted cited evidence for the explanation and preserve "
                        "Claim/Citation identifiers."
                    ),
                    required_tools=["research.ask"],
                    prohibited_actions=["use_unsupported_claim"],
                ),
            ],
        )
        await self.store.create_policy_version(
            policy_id=policy.id,
            family=policy.family,
            version=policy.version,
            status=policy.status,
            policy_json=policy.model_dump_json(),
            created_at=policy.created_at,
        )
        return policy

    async def list_policies(self) -> list[PolicyVersion]:
        return [
            PolicyVersion.model_validate_json(item)
            for item in await self.store.list_policy_versions()
        ]

    async def get_policy(self, policy_id: str) -> PolicyVersion | None:
        return next(
            (item for item in await self.list_policies() if item.id == policy_id),
            None,
        )

    async def active_policy(self) -> PolicyVersion | None:
        policies = [
            item
            for item in await self.list_policies()
            if item.status == PolicyStatus.ACTIVE
        ]
        return max(policies, key=lambda item: item.version) if policies else None

    async def revise_policy(
        self, policy_id: str, request: PolicyRevisionRequest
    ) -> PolicyVersion:
        policies = await self.list_policies()
        base = next((item for item in policies if item.id == policy_id), None)
        if base is None:
            raise LookupError(f"Policy {policy_id} was not found")
        if base.version != request.expected_version:
            raise RuntimeError("Policy revision version conflict")
        experiments = await self.list_experiments()
        source = next(
            (
                item
                for item in experiments
                if item.manifest.id == request.source_experiment_id
            ),
            None,
        )
        if source is None or not source.promotable:
            raise ValueError("Policy revision requires a promotable holdout experiment")
        family = [item for item in policies if item.family == base.family]
        revised = PolicyVersion(
            family=base.family,
            version=max(item.version for item in family) + 1,
            status=PolicyStatus.CANDIDATE,
            alpha=request.alpha,
            epsilon=request.epsilon,
            arms=request.arms,
            source_experiment_id=request.source_experiment_id,
        )
        created = await self.store.create_policy_version(
            policy_id=revised.id,
            family=revised.family,
            version=revised.version,
            status=revised.status,
            policy_json=revised.model_dump_json(),
            created_at=revised.created_at,
        )
        if not created:
            raise RuntimeError("Policy revision conflict")
        return revised

    async def activate_policy(
        self, policy_id: str, request: PolicyActivationRequest
    ) -> PolicyVersion:
        policies = await self.list_policies()
        target = next((item for item in policies if item.id == policy_id), None)
        if target is None:
            raise LookupError(f"Policy {policy_id} was not found")
        if target.version != request.expected_version:
            raise RuntimeError("Policy activation version conflict")
        if target.status not in {PolicyStatus.CANDIDATE, PolicyStatus.DISABLED}:
            raise RuntimeError(f"Policy {target.status} cannot be activated")
        if target.status == PolicyStatus.CANDIDATE:
            experiments = await self.list_experiments()
            report = next(
                (
                    item
                    for item in experiments
                    if item.manifest.id == target.source_experiment_id
                ),
                None,
            )
            if report is None or not report.promotable:
                raise ValueError("Candidate Policy has no promotable holdout report")
        current_active = [
            item
            for item in policies
            if item.family == target.family and item.status == PolicyStatus.ACTIVE
        ]
        active = target.model_copy(update={"status": PolicyStatus.ACTIVE})
        # Policy switch 必须是单个 SQLite transaction；否则进程在“旧版本已停用、
        # 新版本未启用”的窗口崩溃会留下没有 Active Policy 的不一致状态。
        changed = await self.store.activate_policy_version(
            family=target.family,
            policy_id=target.id,
            expected_status=target.status,
            expected_version=target.version,
            policy_json=active.model_dump_json(),
            disabled_policy_json={
                item.id: item.model_copy(
                    update={"status": PolicyStatus.DISABLED}
                ).model_dump_json()
                for item in current_active
            },
        )
        if not changed:
            raise RuntimeError("Policy activation conflict")
        return active

    async def select_strategy(
        self,
        *,
        run_id: str,
        plan_step_id: str,
        decision_point_id: str | None = None,
        context: TeachingContext,
        allowed_tools: set[str],
    ) -> BanditDecision | None:
        """Choose an eligible arm with replay-safe epsilon-greedy LinUCB.

        A stable hash supplies exploration instead of process-global randomness, so a
        crash/retry cannot choose a different arm or corrupt propensity logging.
        """

        if not self.enabled:
            return None
        replay_key = decision_point_id or plan_step_id
        existing = await self.store.get_bandit_decision(run_id, replay_key)
        if existing is not None:
            return BanditDecision.model_validate_json(existing)
        policy = await self.active_policy()
        if policy is None:
            return None
        eligible = [
            arm
            for arm in policy.arms
            if set(arm.required_tools).issubset(allowed_tools)
            and (arm.id != "worked_example" or context.consecutive_failures > 0)
        ]
        if not eligible:
            return None
        # Learned Policy 只在候选 Teaching Arms 内做选择；Tool allowlist、Budget、
        # Verifier 与审批边界仍由 deterministic control plane 强制执行。
        vector = context.vector()
        scored: list[tuple[TeachingArm, float]] = []
        for arm in eligible:
            matrix, target = await self._arm_statistics(policy.id, arm.id)
            inverse = _invert(matrix)
            theta = _matvec(inverse, target)
            confidence = math.sqrt(max(0.0, _quadratic(vector, inverse)))
            scored.append((arm, _dot(theta, vector) + policy.alpha * confidence))
        scored.sort(key=lambda item: (-item[1], item[0].id))
        greedy = scored[0]
        bucket = _stable_fraction(f"{run_id}:{replay_key}:{policy.id}")
        exploratory = bucket < policy.epsilon and len(scored) > 1
        if exploratory:
            index = int(
                _stable_fraction(f"explore:{run_id}:{replay_key}") * len(scored)
            )
            selected = scored[min(index, len(scored) - 1)]
        else:
            selected = greedy
        uniform = policy.epsilon / len(scored)
        propensity = uniform + (
            1 - policy.epsilon if selected[0].id == greedy[0].id else 0
        )
        decision = BanditDecision(
            run_id=run_id,
            plan_step_id=plan_step_id,
            decision_point_id=replay_key,
            policy_id=policy.id,
            policy_version=policy.version,
            arm_id=selected[0].id,
            context=context,
            propensity=propensity,
            score=selected[1],
            exploratory=exploratory,
        )
        created = await self.store.save_bandit_decision(
            run_id=run_id,
            plan_step_id=plan_step_id,
            decision_point_id=replay_key,
            decision_id=decision.id,
            policy_id=policy.id,
            decision_json=decision.model_dump_json(),
        )
        if not created:
            replayed = await self.store.get_bandit_decision(run_id, replay_key)
            if replayed is None:
                raise RuntimeError("Bandit decision disappeared after a write race")
            return BanditDecision.model_validate_json(replayed)
        return decision

    async def evaluate_terminal_run(self, run_id: str) -> RewardRecord | None:
        existing = await self.store.get_reward(run_id)
        if existing is not None:
            return RewardRecord.model_validate_json(existing)
        run = await self.store.get(run_id)
        state_raw = await self.store.load_dynamic_state(run_id)
        if (
            run is None
            or state_raw is None
            or run.status not in {"completed", "failed"}
        ):
            return None
        state = DynamicAgentState.model_validate_json(state_raw)
        events = await self.store.list_events(run_id)
        verifiers = [item for item in events if item.event == "verification_completed"]
        passed = sum(str(item.data.get("status")) == "passed" for item in verifiers)
        safety_violations = _safety_violations(events)
        model_calls = await self.store.list_model_calls(run_id)
        tool_calls = await self.store.list_tool_calls(run_id)
        latency_ms = sum(item.duration_ms for item in model_calls) + sum(
            item.duration_ms or 0 for item in tool_calls
        )
        components = RewardComponents(
            task_completion=1.0 if run.status == "completed" else 0.0,
            immediate_verification=(passed / len(verifiers) if verifiers else 0.0),
            token_efficiency=_efficiency(
                state.usage.total_tokens, state.budget.max_total_tokens
            ),
            tool_efficiency=_efficiency(
                state.usage.tool_calls, max(1, state.budget.max_tool_calls)
            ),
            latency_efficiency=_efficiency(latency_ms, self.expected_latency_ms),
        )
        hard_gate_passed = not safety_violations
        # Safety 是 non-compensable hard gate：违规轨迹永远不会获得可供优化的
        # scalar reward，即使即时正确率或延迟学习指标很高也不能抵消。
        reward = RewardRecord(
            run_id=run_id,
            status=(
                RewardStatus.PROVISIONAL
                if hard_gate_passed
                else RewardStatus.INELIGIBLE
            ),
            components=components,
            safety_violations=safety_violations,
            hard_gate_passed=hard_gate_passed,
            evidence=[
                RewardEvidence(
                    source="agent_event",
                    reference=f"event:{item.sequence}",
                    summary=f"Verifier status: {item.data.get('status', 'unknown')}",
                )
                for item in verifiers
            ],
        )
        await self.store.save_reward(
            reward_id=reward.id,
            run_id=run_id,
            status=reward.status,
            reward_json=reward.model_dump_json(),
            created_at=reward.created_at,
        )
        return reward

    async def submit_delayed_outcome(
        self, run_id: str, outcome: DelayedLearningOutcome
    ) -> RewardRecord:
        reward = await self.evaluate_terminal_run(run_id)
        if reward is None:
            raise ValueError("Run is not terminal or has no Dynamic Agent state")
        if reward.status == RewardStatus.INELIGIBLE:
            raise ValueError("Unsafe Run is permanently ineligible for optimization")
        components = reward.components.model_copy(
            update={
                "delayed_retention": outcome.retention,
                "transfer": outcome.transfer,
                "user_feedback": outcome.user_feedback,
            }
        )
        matured = reward.model_copy(
            update={
                "status": RewardStatus.MATURE,
                "components": components,
                "optimization_score": _optimization_score(components),
                "evidence": [
                    *reward.evidence,
                    RewardEvidence(
                        source="delayed_learning_outcome",
                        reference=outcome.evidence_reference,
                        summary=(
                            f"retention={outcome.retention}, "
                            f"transfer={outcome.transfer}"
                        ),
                    ),
                ],
                "matured_at": datetime.now(UTC),
            }
        )
        changed = await self.store.replace_reward(
            run_id=run_id,
            expected_status=RewardStatus.PROVISIONAL,
            status=matured.status,
            reward_json=matured.model_dump_json(),
        )
        if not changed:
            stored = await self.store.get_reward(run_id)
            if stored is None:
                raise RuntimeError("Reward disappeared during maturation")
            current = RewardRecord.model_validate_json(stored)
            if current.status == RewardStatus.MATURE:
                # Reward 可能已持久化但进程在更新 Bandit 前崩溃。重试时继续执行
                # idempotent credit assignment，不能静默丢失这条学习信号。
                await self._apply_mature_reward(run_id, current)
                return current
            raise RuntimeError("Reward maturation conflict")
        await self._apply_mature_reward(run_id, matured)
        return matured

    async def _apply_mature_reward(self, run_id: str, reward: RewardRecord) -> None:
        if reward.optimization_score is None:
            return
        async with self._update_lock:
            for raw in await self.store.list_bandit_decisions(run_id=run_id):
                decision = BanditDecision.model_validate_json(raw)
                updated = decision.model_copy(update={"reward_id": reward.id})
                # Store 在同一 transaction 内检查 reward_id、更新 sufficient
                # statistics 并绑定 Decision，因此跨进程重试也是 exactly-once。
                await self.store.apply_bandit_reward(
                    decision_id=decision.id,
                    policy_id=decision.policy_id,
                    arm_id=decision.arm_id,
                    vector=decision.context.vector(),
                    reward=reward.optimization_score,
                    decision_json=updated.model_dump_json(),
                    reward_id=reward.id,
                )

    async def _arm_statistics(
        self, policy_id: str, arm_id: str
    ) -> tuple[list[list[float]], list[float]]:
        stored = await self.store.get_bandit_statistics(policy_id, arm_id)
        if stored is None:
            matrix = [
                [1.0 if row == column else 0.0 for column in range(_FEATURE_DIMENSION)]
                for row in range(_FEATURE_DIMENSION)
            ]
            return matrix, [0.0] * _FEATURE_DIMENSION
        raw_matrix = cast(list[list[float]], stored["matrix"])
        raw_target = cast(list[float], stored["target"])
        return raw_matrix, raw_target

    async def save_review(self, review: TrajectoryReview) -> None:
        await self.store.save_trajectory_review(
            run_id=review.run_id,
            decision=review.decision,
            review_json=review.model_dump_json(),
            created_at=review.created_at,
        )

    async def export_sft(self) -> list[SFTTrajectory]:
        """Export only human-approved, mature, safe, fully verified trajectories."""

        output: list[SFTTrajectory] = []
        for run in await self.store.list_runs(limit=1_000):
            raw_review = await self.store.get_trajectory_review(run.run_id)
            raw_reward = await self.store.get_reward(run.run_id)
            if raw_review is None or raw_reward is None or run.status != "completed":
                continue
            review = TrajectoryReview.model_validate_json(raw_review)
            reward = RewardRecord.model_validate_json(raw_reward)
            if (
                review.decision != "approved"
                or reward.status != RewardStatus.MATURE
                or not reward.hard_gate_passed
            ):
                continue
            events = await self.store.list_events(run.run_id)
            verifiers = [
                item for item in events if item.event == "verification_completed"
            ]
            if not verifiers or any(
                str(item.data.get("status")) != "passed" for item in verifiers
            ):
                continue
            transitions = _transitions(run.run_id, events, reward)
            if not transitions:
                continue
            model_calls = await self.store.list_model_calls(run.run_id)
            tools = await self.store.list_tool_calls(run.run_id)
            output.append(
                SFTTrajectory(
                    run_id=run.run_id,
                    policy_version="dynamic_v2",
                    prompt_versions=sorted(
                        {
                            f"{item.prompt_name}@{item.prompt_version}"
                            for item in model_calls
                        }
                    ),
                    tool_names=sorted({item.tool_name for item in tools}),
                    transitions=transitions,
                    reward_id=reward.id,
                    review=review,
                )
            )
        return output

    async def create_preference_pair(
        self,
        *,
        context_fingerprint: str,
        chosen_run_id: str,
        rejected_run_id: str,
        evidence_note: str,
    ) -> PreferencePair:
        chosen = await self._mature_safe_reward(chosen_run_id)
        rejected = await self._mature_safe_reward(rejected_run_id)
        assert chosen.optimization_score is not None
        assert rejected.optimization_score is not None
        margin = chosen.optimization_score - rejected.optimization_score
        if margin <= 0:
            raise ValueError("Chosen trajectory must have a higher mature reward")
        pair = PreferencePair(
            context_fingerprint=context_fingerprint,
            chosen_run_id=chosen_run_id,
            rejected_run_id=rejected_run_id,
            chosen_reward_id=chosen.id,
            rejected_reward_id=rejected.id,
            margin=margin,
            evidence_note=evidence_note,
        )
        await self.store.save_preference_pair(pair.id, pair.model_dump_json())
        return pair

    async def list_preference_pairs(self) -> list[PreferencePair]:
        return [
            PreferencePair.model_validate_json(item)
            for item in await self.store.list_preference_pairs()
        ]

    async def analyze_failures(self) -> list[FailureCluster]:
        """Cluster only evidence-bound failure Reflections, never hidden reasoning."""

        reflections = [
            RunReflection.model_validate_json(item)
            for item in await self.store.list_reflections(
                outcome="failure", limit=1_000
            )
        ]
        groups: dict[tuple[str, tuple[str, ...]], list[RunReflection]] = {}
        for reflection in reflections:
            causes = tuple(
                sorted(
                    _normalize_cause(item.statement)
                    for item in reflection.root_causes
                )
            )
            groups.setdefault((reflection.problem_category, causes), []).append(
                reflection
            )
        clusters: list[FailureCluster] = []
        for (category, causes), items in groups.items():
            signature = hashlib.sha256(
                json.dumps([category, causes], ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            clusters.append(
                FailureCluster(
                    signature=signature,
                    problem_category=category,
                    count=len(items),
                    run_ids=sorted(item.run_id for item in items),
                    root_causes=list(causes),
                    evidence_references=sorted(
                        {
                            f"{item.run_id}:event:{evidence.event_sequence}"
                            for item in items
                            for evidence in item.evidence
                        }
                    ),
                )
            )
        return sorted(clusters, key=lambda item: (-item.count, item.signature))

    async def _mature_safe_reward(self, run_id: str) -> RewardRecord:
        raw = await self.store.get_reward(run_id)
        if raw is None:
            raise ValueError(f"Run {run_id} has no Reward")
        reward = RewardRecord.model_validate_json(raw)
        if reward.status != RewardStatus.MATURE or not reward.hard_gate_passed:
            raise ValueError(f"Run {run_id} does not have a mature safe Reward")
        return reward

    async def evaluate_experiment(
        self,
        manifest: ExperimentManifest,
        samples: list[ReplaySample],
        *,
        split: str = "holdout",
    ) -> ExperimentReport:
        """Run IPS/SNIPS off-policy evaluation with hard promotion gates."""

        selected = [item for item in samples if item.split == split]
        if not selected:
            raise ValueError(f"Replay dataset has no {split} samples")
        # Importance weight 用 logging propensity 校正 candidate policy 与
        # behavior policy 的分布偏移；SNIPS 降低有限样本下的方差。
        weights = [
            item.candidate_probabilities.get(item.logged_arm, 0.0)
            / item.logged_propensity
            for item in selected
        ]
        baseline = sum(item.reward for item in selected) / len(selected)
        ips = sum(
            weight * item.reward for weight, item in zip(weights, selected, strict=True)
        ) / len(selected)
        weight_sum = sum(weights)
        snips = (
            sum(
                weight * item.reward
                for weight, item in zip(weights, selected, strict=True)
            )
            / weight_sum
            if weight_sum
            else 0.0
        )
        ess = (
            weight_sum**2 / sum(weight**2 for weight in weights) if any(weights) else 0
        )
        baseline_tokens = sum(item.tokens for item in selected)
        weighted_tokens = sum(
            weight * item.tokens for weight, item in zip(weights, selected, strict=True)
        )
        token_ratio = (
            weighted_tokens / max(weight_sum, 1e-12) / (baseline_tokens / len(selected))
            if baseline_tokens
            else 1.0
        )
        safety = sum(
            not item.hard_gate_passed and weight > 0
            for weight, item in zip(weights, selected, strict=True)
        )
        standard_error = _weighted_standard_error(selected, weights, snips)
        reasons: list[str] = []
        if split != "holdout":
            reasons.append("promotion requires the frozen holdout split")
        if ess < manifest.minimum_effective_sample_size:
            reasons.append("effective sample size is below the promotion gate")
        if safety > manifest.maximum_safety_violations:
            reasons.append("safety hard gate failed")
        if token_ratio > manifest.maximum_token_ratio:
            reasons.append("token cost ratio exceeded the promotion gate")
        if snips - baseline < manifest.minimum_reward_lift:
            reasons.append("reward lift is below the promotion gate")
        report = ExperimentReport(
            manifest=manifest.model_copy(
                update={
                    "status": (
                        ExperimentStatus.PROMOTABLE
                        if not reasons
                        else ExperimentStatus.REJECTED
                    )
                }
            ),
            split=split,
            sample_count=len(selected),
            effective_sample_size=ess,
            baseline_reward=baseline,
            ips_reward=ips,
            snips_reward=snips,
            reward_lift=snips - baseline,
            token_ratio=token_ratio,
            safety_violations=safety,
            confidence_low=snips - 1.96 * standard_error,
            confidence_high=snips + 1.96 * standard_error,
            promotable=not reasons,
            rejection_reasons=reasons,
        )
        await self.store.save_policy_experiment(
            experiment_id=manifest.id,
            status=report.manifest.status,
            manifest_json=manifest.model_dump_json(),
            report_json=report.model_dump_json(),
        )
        return report

    async def list_experiments(self) -> list[ExperimentReport]:
        return [
            ExperimentReport.model_validate_json(item)
            for item in await self.store.list_policy_experiments()
        ]


def teaching_context(
    state: DynamicAgentState, *, step_index: int, allowed_tools: set[str]
) -> TeachingContext:
    write_tools = {"exercise.grade", "review.schedule", "session.complete"}
    return TeachingContext(
        progress=step_index / max(1, len(state.plan.steps)),
        consecutive_failures=min(1.0, state.consecutive_failures / 3),
        retrieval_available=float("research.ask" in allowed_tools),
        write_step=float(bool(allowed_tools & write_tools)),
    )


def _transitions(
    run_id: str, events: list[AgentEvent], reward: RewardRecord
) -> list[AgentTransition]:
    transitions: list[AgentTransition] = []
    action_indexes = [
        index for index, event in enumerate(events) if event.event == "action_decided"
    ]
    last_action_index = action_indexes[-1] if action_indexes else -1
    for index, event in enumerate(events):
        if event.event != "action_decided":
            continue
        next_action = next(
            (value for value in action_indexes if value > index), len(events)
        )
        later = events[index + 1 : next_action]
        observation = next(
            (item for item in later if item.event == "observation_recorded"), None
        )
        verifier = next(
            (item for item in later if item.event == "verification_completed"), None
        )
        transitions.append(
            AgentTransition(
                run_id=run_id,
                sequence=event.sequence,
                state_ref=f"run:{run_id}:before-event:{event.sequence}",
                action=event.data,
                observation=observation.data if observation is not None else None,
                verifier=verifier.data if verifier is not None else None,
                reward=(
                    reward.optimization_score if index == last_action_index else 0.0
                ),
            )
        )
    return transitions


def _optimization_score(components: RewardComponents) -> float:
    assert components.delayed_retention is not None
    assert components.transfer is not None
    feedback = (
        (components.user_feedback + 1) / 2
        if components.user_feedback is not None
        else 0.5
    )
    return (
        0.20 * components.task_completion
        + 0.15 * components.immediate_verification
        + 0.25 * components.delayed_retention
        + 0.20 * components.transfer
        + 0.10 * feedback
        + 0.04 * components.token_efficiency
        + 0.03 * components.tool_efficiency
        + 0.03 * components.latency_efficiency
    )


def _safety_violations(events: list[AgentEvent]) -> list[str]:
    violations: list[str] = []
    for event in events:
        if event.event == "action_rejected":
            reason = str(event.data.get("reason", "")).casefold()
            if any(
                token in reason
                for token in ("unavailable", "permission", "allowlist", "outside")
            ):
                violations.append(f"event:{event.sequence}:{reason[:160]}")
        if event.event in {"tool_completed", "observation_recorded"} and str(
            event.data.get("error_kind", "")
        ) == "permission_denied":
            violations.append(f"event:{event.sequence}:tool permission denied")
    return violations


def _normalize_cause(value: str) -> str:
    return " ".join(value.casefold().split())[:500]


def _efficiency(actual: float, maximum: float) -> float:
    return max(0.0, min(1.0, 1 - actual / max(maximum, 1e-12)))


def _stable_fraction(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _matvec(matrix: list[list[float]], vector: list[float]) -> list[float]:
    return [_dot(row, vector) for row in matrix]


def _quadratic(vector: list[float], matrix: list[list[float]]) -> float:
    return _dot(vector, _matvec(matrix, vector))


def _invert(matrix: list[list[float]]) -> list[list[float]]:
    """用 Gauss-Jordan elimination 求小型 positive-definite matrix 的逆。"""

    size = len(matrix)
    augmented = [
        [*row, *[1.0 if index == column else 0.0 for column in range(size)]]
        for index, row in enumerate(matrix)
    ]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("Bandit covariance matrix is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(
                    augmented[row], augmented[column], strict=True
                )
            ]
    return [row[size:] for row in augmented]


def _weighted_standard_error(
    samples: list[ReplaySample], weights: list[float], mean: float
) -> float:
    weight_sum = sum(weights)
    if weight_sum <= 0:
        return 0.0
    variance = (
        sum(
            weight * (sample.reward - mean) ** 2
            for weight, sample in zip(weights, samples, strict=True)
        )
        / weight_sum
    )
    effective = weight_sum**2 / max(sum(weight**2 for weight in weights), 1e-12)
    return math.sqrt(variance / max(effective, 1.0))
