"""Seeded stochastic trial runner and pass-k aggregation."""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Awaitable, Callable
from math import ceil
from typing import Protocol

from app.agent.reliability.faults import FaultController, InjectedFault
from app.agent.reliability.graders import ReliabilityGrader
from app.agent.reliability.models import (
    EnvironmentSnapshot,
    FaultKind,
    ReliabilityMetrics,
    ReliabilityReport,
    ReliabilityRunRequest,
    ReliabilityScenario,
    ReliabilityTrial,
    ScheduledFault,
    SliceMetric,
    TrajectoryStep,
    TrialManifest,
    TrialOutcome,
)


class ScenarioExecutor(Protocol):
    async def execute(
        self,
        scenario: ReliabilityScenario,
        manifest: TrialManifest,
        faults: FaultController,
    ) -> TrialOutcome: ...


ReportObserver = Callable[[ReliabilityReport], Awaitable[None]]


class ReliabilityRunner:
    def __init__(
        self,
        executor: ScenarioExecutor,
        *,
        grader: ReliabilityGrader | None = None,
        observer: ReportObserver | None = None,
    ) -> None:
        self.executor = executor
        self.grader = grader or ReliabilityGrader()
        self.observer = observer

    async def run(self, request: ReliabilityRunRequest) -> ReliabilityReport:
        trials: list[ReliabilityTrial] = []
        for scenario in request.scenarios:
            for trial_index in range(request.trials_per_scenario):
                seed = _trial_seed(request.base_seed, scenario, trial_index)
                manifest = build_manifest(
                    scenario,
                    trial_index=trial_index,
                    seed=seed,
                    environment=request.environment,
                )
                controller = FaultController(manifest)
                outcome = await self.executor.execute(
                    scenario, manifest, controller
                )
                grade = self.grader.grade(scenario, outcome)
                trials.append(
                    ReliabilityTrial(
                        manifest=manifest,
                        scenario=scenario,
                        outcome=outcome,
                        grade=grade,
                    )
                )
        slices = _slices(trials)
        report = ReliabilityReport(
            base_seed=request.base_seed,
            trials_per_scenario=request.trials_per_scenario,
            fixture_only=all(item.fixture_only for item in request.scenarios),
            metrics=_metrics(trials, slices),
            slices=slices,
            trials=trials,
        )
        if self.observer is not None:
            await self.observer(report)
        return report


class ContractScenarioExecutor:
    """离线 Control-plane Executor；其结果不得解释为生产 efficacy。"""

    async def execute(
        self,
        scenario: ReliabilityScenario,
        manifest: TrialManifest,
        faults: FaultController,
    ) -> TrialOutcome:
        trajectory: list[TrajectoryStep] = []
        failed = False
        index = 0
        for scheduled in manifest.faults:
            if not scheduled.scheduled:
                continue
            spec = scheduled.spec
            if spec.kind == FaultKind.PROMPT_INJECTION:
                record = faults.block(spec.kind, spec.target)
                trajectory.append(
                    TrajectoryStep(
                        index=index,
                        action=f"block:{spec.kind.value}",
                        status="blocked",
                    )
                )
                index += 1
                if record is None:
                    continue
                continue
            try:
                faults.hit(spec.kind, spec.target)
            except InjectedFault as error:
                if spec.recoverable:
                    faults.recover(error.record.fault_id)
                    trajectory.append(
                        TrajectoryStep(
                            index=index,
                            action=f"recover:{spec.kind.value}",
                            status="recovered",
                        )
                    )
                else:
                    failed = True
                    trajectory.append(
                        TrajectoryStep(
                            index=index,
                            action=f"fault:{spec.kind.value}",
                            status="failed",
                        )
                    )
                index += 1
        if not failed:
            trajectory.append(
                TrajectoryStep(
                    index=index,
                    action="finalize_with_required_evidence",
                    status="succeeded",
                    evidence_ids=scenario.required_evidence_ids,
                    tokens=min(500, scenario.max_tokens),
                    estimated_cost_usd=min(
                        0.01, scenario.max_estimated_cost_usd
                    ),
                )
            )
        return TrialOutcome(
            final_status="failed" if failed else "completed",
            actual_evidence_ids=(
                [] if failed else scenario.required_evidence_ids
            ),
            trajectory=trajectory,
            faults=faults.records(),
            total_tokens=sum(item.tokens for item in trajectory),
            estimated_cost_usd=sum(
                item.estimated_cost_usd for item in trajectory
            ),
            terminal_reason="injected_unrecoverable_fault" if failed else None,
        )


def build_manifest(
    scenario: ReliabilityScenario,
    *,
    trial_index: int,
    seed: int,
    environment: EnvironmentSnapshot,
) -> TrialManifest:
    # 每个 Fault 消耗固定顺序的 PRNG 值；同一 Scenario/seed/environment 因而能
    # 重建完全相同的 schedule，canonical hash 则用于证明 Replay 输入未漂移。
    rng = random.Random(seed)
    scheduled = [
        ScheduledFault(
            spec=spec,
            random_value=(value := rng.random()),
            scheduled=value <= spec.probability,
        )
        for spec in scenario.faults
    ]
    canonical = {
        "scenario_id": scenario.id,
        "scenario_version": scenario.version,
        "trial_index": trial_index,
        "seed": seed,
        "environment": environment.model_dump(mode="json"),
        "faults": [item.model_dump(mode="json") for item in scheduled],
    }
    digest = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return TrialManifest(
        scenario_id=scenario.id,
        scenario_version=scenario.version,
        trial_index=trial_index,
        seed=seed,
        environment=environment,
        faults=scheduled,
        manifest_hash=digest,
    )


def _trial_seed(
    base_seed: int, scenario: ReliabilityScenario, trial_index: int
) -> int:
    digest = hashlib.sha256(
        f"{base_seed}:{scenario.id}:{scenario.version}:{trial_index}".encode()
    ).hexdigest()
    return int(digest[:15], 16)


def _metrics(
    trials: list[ReliabilityTrial], slices: list[SliceMetric]
) -> ReliabilityMetrics:
    # pass@k 与 pass^k 使用 observed semantics：前者要求同一 Scenario 至少一次
    # 成功，后者要求全部 Trial 成功；不假设 Trial 是 i.i.d. 样本。
    grouped: defaultdict[tuple[str, str], list[ReliabilityTrial]] = defaultdict(list)
    for trial in trials:
        grouped[(trial.scenario.id, trial.scenario.version)].append(trial)
    pass_at_k = sum(
        any(item.grade.passed for item in group) for group in grouped.values()
    )
    pass_power_k = sum(
        all(item.grade.passed for item in group) for group in grouped.values()
    )
    injected = [record for trial in trials for record in trial.outcome.faults]
    actions = [step for trial in trials for step in trial.outcome.trajectory]
    costs = [trial.outcome.estimated_cost_usd for trial in trials]
    tokens = [float(trial.outcome.total_tokens) for trial in trials]
    return ReliabilityMetrics(
        pass_at_k=pass_at_k / len(grouped),
        pass_power_k=pass_power_k / len(grouped),
        trial_pass_rate=sum(item.grade.passed for item in trials) / len(trials),
        recovery_rate=(
            sum(item.recovered for item in injected) / len(injected)
            if injected
            else 1.0
        ),
        redundancy_rate=(
            sum(item.duplicate for item in actions) / len(actions)
            if actions
            else 0.0
        ),
        safety_rate=sum(item.grade.safety_passed for item in trials) / len(trials),
        cost_p95_usd=_percentile(costs, 0.95),
        tokens_p95=_percentile(tokens, 0.95),
        worst_slice_score=min(item.average_score for item in slices),
    )


def _slices(trials: list[ReliabilityTrial]) -> list[SliceMetric]:
    # 一个 Trial 可同时命中多个 Fault slice。最差 slice 会在聚合层成为 release
    # gate，避免总体平均值掩盖特定 Role、Variant 或 Fault 的系统性退化。
    buckets: defaultdict[tuple[str, str], list[ReliabilityTrial]] = defaultdict(list)
    for trial in trials:
        buckets[("task_kind", trial.scenario.task_kind)].append(trial)
        buckets[("role", trial.scenario.role)].append(trial)
        buckets[("variant", trial.scenario.variant.value)].append(trial)
        scheduled_kinds = {
            item.spec.kind.value for item in trial.manifest.faults if item.scheduled
        } or {"none"}
        for kind in scheduled_kinds:
            buckets[("fault", kind)].append(trial)
    return [
        SliceMetric(
            dimension=dimension,  # type: ignore[arg-type]
            value=value,
            trials=len(items),
            pass_rate=sum(item.grade.passed for item in items) / len(items),
            safety_rate=sum(item.grade.safety_passed for item in items) / len(items),
            average_score=sum(item.grade.score for item in items) / len(items),
        )
        for (dimension, value), items in sorted(buckets.items())
    ]


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, ceil(percentile * len(ordered)) - 1)]
