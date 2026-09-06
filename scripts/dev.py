"""Cross-platform development process launcher."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def require_command(command: str) -> str:
    resolved = shutil.which(command)
    if resolved is None:
        raise SystemExit(
            f"Required command '{command}' was not found on PATH. "
            "See README.md for prerequisites."
        )
    return resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start LearnLoop development services."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--backend-only", action="store_true")
    group.add_argument("--frontend-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    commands: list[tuple[list[str], Path]] = []

    if not args.frontend_only:
        commands.append(
            (
                [
                    require_command("uv"),
                    "run",
                    "uvicorn",
                    "app.main:app",
                    "--reload",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ],
                PROJECT_ROOT / "backend",
            )
        )

    if not args.backend_only:
        commands.append(
            (
                [require_command("corepack"), "pnpm", "dev"],
                PROJECT_ROOT / "frontend",
            )
        )

    processes = [subprocess.Popen(command, cwd=cwd) for command, cwd in commands]

    try:
        while processes:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    return return_code
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    return 0


if __name__ == "__main__":
    sys.exit(main())
