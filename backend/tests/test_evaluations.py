"""Offline evaluation metrics and regression-gate tests."""

from datetime import UTC, datetime
from pathlib import Path

from evals.runner import run, write_report

DATASET = Path(__file__).parents[1] / "evals" / "datasets" / "v1.json"
MEMORY_DATASET = (
    Path(__file__).parents[1] / "evals" / "datasets" / "memory_v1.json"
)
RESEARCH_DATASET = (
    Path(__file__).parents[1] / "evals" / "datasets" / "research_v1.json"
)


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
