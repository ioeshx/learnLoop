"""Alembic smoke test against an isolated SQLite file."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "users",
    "learning_goals",
    "knowledge_nodes",
    "knowledge_edges",
    "study_plans",
    "plan_items",
    "study_sessions",
    "exercises",
    "exercise_attempts",
    "mastery_events",
    "mastery_snapshots",
    "review_schedules",
    "learning_resources",
    "document_chunks",
    "document_chunks_fts",
}


def test_upgrade_creates_the_initial_schema(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    environment = os.environ.copy()
    environment["LEARNLOOP_ENVIRONMENT"] = "test"
    environment["LEARNLOOP_DATA_DIR"] = str(data_dir)

    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "migrate.py"), "upgrade"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
    )

    with sqlite3.connect(data_dir / "db" / "learnloop.db") as connection:
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()

    assert {row[0] for row in table_rows} >= EXPECTED_TABLES
    assert version == ("0004_learning_resources_rag",)
    assert journal_mode == ("wal",)
    assert busy_timeout == (5000,)
