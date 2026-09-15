"""Deterministic offline evaluators for LearnLoop's first quality baseline."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any


@dataclass(frozen=True, slots=True)
class Metric:
    name: str
    value: float
    threshold: float
    higher_is_better: bool = True

    @property
    def passed(self) -> bool:
        if self.higher_is_better:
            return self.value >= self.threshold
        return self.value <= self.threshold

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "value": round(self.value, 6),
            "threshold": self.threshold,
            "higher_is_better": self.higher_is_better,
            "passed": self.passed,
        }


def evaluate(dataset: dict[str, Any]) -> list[Metric]:
    if dataset.get("suite") == "agent_memory":
        return _evaluate_memory(dataset)
    if dataset.get("suite") == "agentic_research":
        return _evaluate_research(dataset)
    if dataset.get("suite") == "subagent_delegation":
        return _evaluate_delegation(dataset)
    if dataset.get("suite") == "reflection_skill_library":
        return _evaluate_reflection_skills(dataset)
    thresholds = dataset["thresholds"]
    retrieval = dataset["retrieval"]
    usage = dataset["model_usage"]
    return [
        _rate_metric(
            "structured_output_valid_rate",
            [bool(case["valid"]) for case in dataset["structured_outputs"]],
            thresholds,
        ),
        _rate_metric(
            "tool_call_accuracy",
            [case["actual"] == case["expected"] for case in dataset["tool_calls"]],
            thresholds,
        ),
        _rate_metric(
            "checkpoint_recovery_rate",
            [bool(case["recovered"]) for case in dataset["checkpoint_recovery"]],
            thresholds,
        ),
        Metric(
            "rag_recall_at_k",
            _recall_at_k(retrieval),
            float(thresholds["rag_recall_at_k"]),
        ),
        Metric("rag_mrr", _mrr(retrieval), float(thresholds["rag_mrr"])),
        _rate_metric(
            "citation_support_accuracy",
            [
                case["predicted_supported"] == case["expected_supported"]
                for case in dataset["citations"]
            ],
            thresholds,
        ),
        _rate_metric(
            "objective_grading_accuracy",
            [
                (set(case["selected"]) == set(case["answer_key"]))
                == case["expected_correct"]
                for case in dataset["objective_grading"]
            ],
            thresholds,
        ),
        _rate_metric(
            "short_answer_consistency_rate",
            [
                max(case["scores"]) - min(case["scores"]) <= case["max_range"]
                for case in dataset["short_answer_consistency"]
            ],
            thresholds,
        ),
        _rate_metric(
            "remediation_success_rate",
            [bool(case["recovered"]) for case in dataset["remediation"]],
            thresholds,
        ),
        Metric(
            "model_latency_p95_ms",
            _percentile([float(case["latency_ms"]) for case in usage], 0.95),
            float(thresholds["model_latency_p95_ms"]),
            higher_is_better=False,
        ),
        Metric(
            "model_tokens_average",
            sum(float(case["tokens"]) for case in usage) / len(usage),
            float(thresholds["model_tokens_average"]),
            higher_is_better=False,
        ),
        Metric(
            "model_cost_average_usd",
            sum(float(case["cost_usd"]) for case in usage) / len(usage),
            float(thresholds["model_cost_average_usd"]),
            higher_is_better=False,
        ),
    ]


def _evaluate_memory(dataset: dict[str, Any]) -> list[Metric]:
    """Evaluate LongMemEval-style recall, update, safety, and forgetting cases."""

    thresholds = dataset["thresholds"]
    cases = dataset["memory_cases"]
    recall_cases = [case for case in cases if case["category"] != "write_safety"]
    abstention_cases = [
        case for case in recall_cases if not case["expected_memory_ids"]
    ]
    write_cases = [case for case in cases if case["category"] == "write_safety"]
    expected_total = sum(len(case["expected_memory_ids"]) for case in recall_cases)
    recalled_expected = sum(
        len(set(case["actual_memory_ids"]) & set(case["expected_memory_ids"]))
        for case in recall_cases
    )
    false_recalled = sum(
        len(set(case["actual_memory_ids"]) - set(case["expected_memory_ids"]))
        for case in recall_cases
    )
    retrieved_total = sum(len(case["actual_memory_ids"]) for case in recall_cases)
    enabled_success = sum(bool(case["task_success_with_memory"]) for case in cases)
    disabled_success = sum(bool(case["task_success_without_memory"]) for case in cases)
    return [
        Metric(
            "memory_recall_accuracy",
            recalled_expected / expected_total if expected_total else 1.0,
            float(thresholds["memory_recall_accuracy"]),
        ),
        Metric(
            "memory_abstention_rate",
            sum(not case["actual_memory_ids"] for case in abstention_cases)
            / len(abstention_cases),
            float(thresholds["memory_abstention_rate"]),
        ),
        Metric(
            "memory_write_safety_rate",
            sum(
                case["actual_status"] == case["expected_status"] for case in write_cases
            )
            / len(write_cases),
            float(thresholds["memory_write_safety_rate"]),
        ),
        Metric(
            "memory_false_recall_rate",
            false_recalled / retrieved_total if retrieved_total else 0.0,
            float(thresholds["memory_false_recall_rate"]),
            higher_is_better=False,
        ),
        Metric(
            "memory_task_success_lift",
            (enabled_success - disabled_success) / len(cases),
            float(thresholds["memory_task_success_lift"]),
        ),
    ]


def _evaluate_research(dataset: dict[str, Any]) -> list[Metric]:
    """Evaluate routing, multi-hop recall, citation safety, and bounded cost."""

    thresholds = dataset["thresholds"]
    cases = dataset["research_cases"]
    retrieval_cases = [
        case for case in cases if case["expected_mode"] != "no_retrieval"
    ]
    relevant_total = sum(len(case["relevant_chunks"]) for case in retrieval_cases)
    recalled = sum(
        len(set(case["retrieved_chunks"]) & set(case["relevant_chunks"]))
        for case in retrieval_cases
    )
    citation_cases = [case for case in cases if case["expected_citations"]]
    return [
        _rate_metric(
            "research_route_accuracy",
            [case["actual_mode"] == case["expected_mode"] for case in cases],
            thresholds,
        ),
        Metric(
            "research_multihop_recall",
            recalled / relevant_total if relevant_total else 1.0,
            float(thresholds["research_multihop_recall"]),
        ),
        _rate_metric(
            "research_citation_support_accuracy",
            [
                set(case["actual_citations"]) == set(case["expected_citations"])
                for case in citation_cases
            ],
            thresholds,
        ),
        _rate_metric(
            "research_safety_rate",
            [not case["unsafe_evidence_in_answer"] for case in cases],
            thresholds,
        ),
        _rate_metric(
            "research_no_retrieval_efficiency",
            [
                case["query_count"] == 0
                for case in cases
                if case["expected_mode"] == "no_retrieval"
            ],
            thresholds,
        ),
        _rate_metric(
            "research_insufficient_evidence_accuracy",
            [
                case["actual_insufficient"] == case["expected_insufficient"]
                for case in cases
            ],
            thresholds,
        ),
        Metric(
            "research_task_success_lift",
            (
                sum(bool(case["success_agentic"]) for case in cases)
                - sum(bool(case["success_single_shot"]) for case in cases)
            )
            / len(cases),
            float(thresholds["research_task_success_lift"]),
        ),
        Metric(
            "research_average_tokens",
            sum(float(case["estimated_tokens"]) for case in cases) / len(cases),
            float(thresholds["research_average_tokens"]),
            higher_is_better=False,
        ),
    ]


def _evaluate_delegation(dataset: dict[str, Any]) -> list[Metric]:
    """Evaluate routing, isolation, lifecycle, fallback, and bounded cost."""

    thresholds = dataset["thresholds"]
    cases = dataset["delegation_cases"]
    simple = [case for case in cases if case["category"] == "simple"]
    cancelled = [case for case in cases if case["parent_cancelled"]]
    duplicates = [case for case in cases if case["duplicate_attempts"] > 1]
    failures = [case for case in cases if case["subagent_failed"]]
    comparative = [case for case in cases if case["single_agent_ms"] > 0]
    single_tokens = sum(float(case["single_agent_tokens"]) for case in comparative)
    return [
        Metric(
            "subagent_simple_delegation_rate",
            sum(bool(case["delegated"]) for case in simple) / len(simple),
            float(thresholds["subagent_simple_delegation_rate"]),
            higher_is_better=False,
        ),
        _rate_metric(
            "subagent_scope_safety_rate",
            [not case["scope_escaped"] for case in cases],
            thresholds,
        ),
        _rate_metric(
            "subagent_cancel_propagation_rate",
            [case["child_cancelled"] for case in cancelled],
            thresholds,
        ),
        _rate_metric(
            "subagent_duplicate_prevention_rate",
            [case["new_child_runs"] == 1 for case in duplicates],
            thresholds,
        ),
        _rate_metric(
            "subagent_fallback_rate",
            [case["lead_fallback"] for case in failures],
            thresholds,
        ),
        Metric(
            "subagent_task_success_lift",
            (
                sum(bool(case["success_delegated"]) for case in cases)
                - sum(bool(case["success_single_agent"]) for case in cases)
            )
            / len(cases),
            float(thresholds["subagent_task_success_lift"]),
        ),
        Metric(
            "subagent_wall_clock_ratio",
            sum(float(case["delegated_ms"]) for case in comparative)
            / sum(float(case["single_agent_ms"]) for case in comparative),
            float(thresholds["subagent_wall_clock_ratio"]),
            higher_is_better=False,
        ),
        Metric(
            "subagent_token_overhead_ratio",
            sum(float(case["delegated_tokens"]) for case in comparative)
            / single_tokens,
            float(thresholds["subagent_token_overhead_ratio"]),
            higher_is_better=False,
        ),
    ]


def _evaluate_reflection_skills(dataset: dict[str, Any]) -> list[Metric]:
    """Evaluate provenance, governed recall, degradation, and ablation value."""

    thresholds = dataset["thresholds"]
    reflections = dataset["reflection_cases"]
    skills = dataset["skill_cases"]
    ablations = dataset["ablation_cases"]
    verified = [case for case in reflections if case["has_verifier_signal"]]
    unverified = [case for case in reflections if not case["has_verifier_signal"]]
    total_without = sum(float(case["tool_calls_without_skill"]) for case in ablations)
    return [
        _rate_metric(
            "reflection_grounding_rate",
            [
                set(case["referenced_evidence_ids"]).issubset(
                    set(case["available_evidence_ids"])
                )
                for case in verified
            ],
            thresholds,
        ),
        Metric(
            "reflection_unverified_generation_rate",
            sum(bool(case["reflection_generated"]) for case in unverified)
            / len(unverified),
            float(thresholds["reflection_unverified_generation_rate"]),
            higher_is_better=False,
        ),
        _rate_metric(
            "skill_recall_precision",
            [case["actual_recalled"] == case["expected_recalled"] for case in skills],
            thresholds,
        ),
        _rate_metric(
            "skill_scope_safety_rate",
            [not case["capability_expanded"] for case in skills],
            thresholds,
        ),
        _rate_metric(
            "skill_review_gate_rate",
            [not case["candidate_executed"] for case in skills],
            thresholds,
        ),
        _rate_metric(
            "skill_degradation_safety_rate",
            [
                not case["recalled_after_quarantine"]
                for case in skills
                if case["degraded"]
            ],
            thresholds,
        ),
        _rate_metric(
            "skill_rollback_success_rate",
            [case["rollback_succeeded"] for case in skills if case["rollback_tested"]],
            thresholds,
        ),
        Metric(
            "skill_task_success_lift",
            (
                sum(bool(case["success_with_skill"]) for case in ablations)
                - sum(bool(case["success_without_skill"]) for case in ablations)
            )
            / len(ablations),
            float(thresholds["skill_task_success_lift"]),
        ),
        Metric(
            "skill_tool_call_ratio",
            sum(float(case["tool_calls_with_skill"]) for case in ablations)
            / total_without,
            float(thresholds["skill_tool_call_ratio"]),
            higher_is_better=False,
        ),
    ]


def _rate_metric(name: str, outcomes: list[bool], thresholds: dict[str, Any]) -> Metric:
    return Metric(name, sum(outcomes) / len(outcomes), float(thresholds[name]))


def _recall_at_k(cases: list[dict[str, Any]]) -> float:
    values = []
    for case in cases:
        relevant = set(case["relevant"])
        retrieved = set(case["ranking"][: int(case["k"])])
        values.append(len(relevant & retrieved) / len(relevant))
    return sum(values) / len(values)


def _mrr(cases: list[dict[str, Any]]) -> float:
    reciprocal_ranks: list[float] = []
    for case in cases:
        relevant = set(case["relevant"])
        rank = next(
            (
                index
                for index, item in enumerate(case["ranking"], 1)
                if item in relevant
            ),
            None,
        )
        reciprocal_ranks.append(0.0 if rank is None else 1.0 / rank)
    return sum(reciprocal_ranks) / len(reciprocal_ranks)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, ceil(percentile * len(ordered)) - 1)
    return ordered[index]
