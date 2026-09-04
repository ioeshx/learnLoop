"""Configuration validation tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_api_prefix_must_start_with_slash() -> None:
    with pytest.raises(ValidationError, match="api_prefix must start"):
        Settings(api_prefix="api/v1")


def test_port_has_clear_validation_error() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 65535"):
        Settings(port=70_000)


def test_database_path_is_derived_from_data_directory(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "runtime")

    assert settings.database_path == tmp_path / "runtime" / "db" / "learnloop.db"
    assert settings.database_url.startswith("sqlite+aiosqlite:///")
    assert settings.database_url.endswith("/runtime/db/learnloop.db")
