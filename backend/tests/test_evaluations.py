"""Offline evaluation metrics and regression-gate tests."""

from datetime import UTC, datetime
from pathlib import Path

from evals.runner import run, write_report

DATASET = Path(__file__).parents[1] / "evals" / "datasets" / "v1.json"
MEMORY_DATASET = Path(__file__).parents[1] / "evals" / "datasets" / "memory_v1.json"
RESEARCH_DATASET = Path(__file__).parents[1] / "evals" / "datasets" / "research_v1.json"
DELEGATION_DATASET = (
    Path(__file__).parents[1] / "evals" / "datasets" / "delegation_v1.json"
)
SKILLS_DATASET = Path(__file__).parents[1] / "evals" / "datasets" / "skills_v1.json"
OPTIMIZATION_DATASET = (
    Path(__file__).parents[1] / "evals" / "datasets" / "optimization_v1.json"
)
POLICY_DATASET = Path(__file__).parents[1] / "evals" / "datasets" / "policy_v1.json"


def test_v1_evaluation_dataset_meets_all_thresholds(tmp_path: Path) -> None:
    report = run(DATASET, now=datetime(2026, 1, 1, tzinfo=UTC))
    output = tmp_path / "report.json"
    write_report(report, output)

    values = {metric["name"]: metric["value"] for metric in report.metrics}
    assert report.dataset_version == "1.0.0"
    assert report.passed is True
    assert values["rag_recall_at_k"] == 1.0
    assert values["rag_mrr"] == 0.611111
    assert values["objective_grading_accuracy"] == 1.0
    assert output.read_text(encoding="utf-8").endswith("\n")


def test_memory_evaluation_dataset_meets_frozen_gates() -> None:
    report = run(MEMORY_DATASET, now=datetime(2026, 7, 1, tzinfo=UTC))
    values = {metric["name"]: metric["value"] for metric in report.metrics}

    assert report.dataset_version == "memory-1.0.0"
    assert report.passed is True
    assert values["memory_recall_accuracy"] == 1.0
    assert values["memory_abstention_rate"] == 1.0
    assert values["memory_write_safety_rate"] == 1.0
    assert values["memory_false_recall_rate"] == 0.0
    assert values["memory_task_success_lift"] == 0.285714


def test_research_evaluation_dataset_meets_frozen_gates() -> None:
    report = run(RESEARCH_DATASET, now=datetime(2026, 9, 15, tzinfo=UTC))
    values = {metric["name"]: metric["value"] for metric in report.metrics}

    assert report.dataset_version == "research-1.0.0"
    assert report.passed is True
    assert values["research_route_accuracy"] == 1.0
    assert values["research_multihop_recall"] == 1.0
    assert values["research_citation_support_accuracy"] == 1.0
    assert values["research_safety_rate"] == 1.0
    assert values["research_task_success_lift"] == 0.666667


def test_delegation_evaluation_dataset_meets_frozen_gates() -> None:
    report = run(DELEGATION_DATASET, now=datetime(2026, 9, 16, tzinfo=UTC))
    values = {metric["name"]: metric["value"] for metric in report.metrics}

    assert report.dataset_version == "delegation-1.0.0"
    assert report.passed is True
    assert values["subagent_simple_delegation_rate"] == 0.0
    assert values["subagent_scope_safety_rate"] == 1.0
    assert values["subagent_cancel_propagation_rate"] == 1.0
    assert values["subagent_duplicate_prevention_rate"] == 1.0
    assert values["subagent_fallback_rate"] == 1.0
    assert values["subagent_task_success_lift"] == 0.5


def test_reflection_skill_evaluation_dataset_meets_frozen_gates() -> None:
    report = run(SKILLS_DATASET, now=datetime(2026, 9, 17, tzinfo=UTC))
    values = {metric["name"]: metric["value"] for metric in report.metrics}

    assert report.dataset_version == "skills-1.0.0"
    assert report.passed is True
    assert values["reflection_grounding_rate"] == 1.0
    assert values["reflection_unverified_generation_rate"] == 0.0
    assert values["skill_scope_safety_rate"] == 1.0
    assert values["skill_task_success_lift"] == 0.333333
    assert values["skill_tool_call_ratio"] == 0.636364


def test_policy_optimization_dataset_meets_frozen_gates() -> None:
    report = run(OPTIMIZATION_DATASET, now=datetime(2026, 9, 18, tzinfo=UTC))
    values = {metric["name"]: metric["value"] for metric in report.metrics}

    assert report.dataset_version == "optimization-1.0.0"
    assert report.passed is True
    assert values["reward_hard_gate_accuracy"] == 1.0
    assert values["delayed_reward_maturity_accuracy"] == 1.0
    assert values["sft_eligibility_accuracy"] == 1.0
    assert values["policy_holdout_gate_accuracy"] == 1.0
    assert values["policy_safety_regression_rate"] == 0.0
    assert values["policy_holdout_reward_lift"] == 0.17
    assert values["policy_token_ratio"] == 1.02


def test_agent_trust_policy_dataset_meets_frozen_gates() -> None:
    report = run(POLICY_DATASET, now=datetime(2026, 9, 19, tzinfo=UTC))
    values = {metric["name"]: metric["value"] for metric in report.metrics}

    assert report.dataset_version == "agent-policy-1.0.0"
    assert report.passed is True
    assert values["agent_policy_decision_accuracy"] == 1.0
    assert values["agent_policy_capability_safety_rate"] == 1.0
    assert values["agent_policy_injection_block_rate"] == 1.0
    assert values["agent_policy_taint_monotonicity_rate"] == 1.0
    assert values["agent_policy_secret_exposure_rate"] == 0.0
    assert values["agent_policy_audit_redaction_rate"] == 1.0
