"""Versioned evaluation runner with threshold-gated JSON reports."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.evaluators import evaluate


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    dataset_version: str
    generated_at: datetime
    metrics: tuple[dict[str, object], ...]

    @property
    def passed(self) -> bool:
        return all(bool(metric["passed"]) for metric in self.metrics)

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset_version": self.dataset_version,
            "generated_at": self.generated_at.isoformat(),
            "passed": self.passed,
            "metrics": list(self.metrics),
        }


def run(dataset_path: Path, *, now: datetime | None = None) -> EvaluationReport:
    decoded: Any = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("evaluation dataset must be a JSON object")
    metrics = tuple(metric.as_dict() for metric in evaluate(decoded))
    return EvaluationReport(
        dataset_version=str(decoded["version"]),
        generated_at=now or datetime.now(UTC),
        metrics=metrics,
    )


def write_report(report: EvaluationReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
