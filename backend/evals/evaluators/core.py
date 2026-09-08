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


def _rate_metric(
    name: str, outcomes: list[bool], thresholds: dict[str, Any]
) -> Metric:
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
