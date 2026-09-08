"""Run LearnLoop's deterministic offline evaluation suite."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from evals.runner import run, write_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=BACKEND_ROOT / "evals" / "datasets" / "v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_ROOT / "evals" / "reports" / "latest.json",
    )
    args = parser.parse_args()
    report = run(args.dataset)
    write_report(report, args.output)
    for metric in report.metrics:
        state = "PASS" if metric["passed"] else "FAIL"
        print(f"{state:4} {metric['name']}: {metric['value']}")
    print(f"report: {args.output}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
