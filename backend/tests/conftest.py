"""Shared backend test fixtures."""

from pathlib import Path

import pytest

from app.config import Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(environment="test", data_dir=tmp_path / "data")
