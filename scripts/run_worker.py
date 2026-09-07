"""Run the standalone LearnLoop SQLite background worker."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from contextlib import suppress
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the LearnLoop job worker.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim at most one job and exit (useful for tests and maintenance).",
    )
    return parser.parse_args()


async def run(*, once: bool) -> int:
    from app.config import Settings
    from app.logging import configure_logging
    from app.workers.bootstrap import open_background_worker

    settings = Settings()
    configure_logging(settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signal_name, stop.set)

    async with open_background_worker(settings) as worker:
        if once:
            await worker.run_once()
        else:
            await worker.run_forever(stop)
    return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(once=args.once))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
