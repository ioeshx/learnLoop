"""Cross-platform Alembic command wrapper."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

if TYPE_CHECKING:
    from alembic.config import Config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the LearnLoop SQLite schema.")
    parser.add_argument(
        "action",
        choices=("upgrade", "downgrade", "current"),
        nargs="?",
        default="upgrade",
    )
    parser.add_argument(
        "revision",
        nargs="?",
        help="Alembic revision (upgrade defaults to head; downgrade defaults to -1).",
    )
    return parser.parse_args()


def build_config(database_url: str) -> "Config":
    from alembic.config import Config

    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    # Alembic stores values in ConfigParser, where percent signs interpolate.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def main() -> int:
    args = parse_args()
    from alembic import command

    from app.config import Settings

    settings = Settings()
    settings.ensure_runtime_directories()
    config = build_config(settings.database_url)

    if args.action == "upgrade":
        command.upgrade(config, args.revision or "head")
    elif args.action == "downgrade":
        command.downgrade(config, args.revision or "-1")
    else:
        command.current(config, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
