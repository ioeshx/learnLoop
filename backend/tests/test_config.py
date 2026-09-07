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
    assert settings.checkpoint_path == (
        tmp_path / "runtime" / "db" / "checkpoints.db"
    )
    assert settings.document_storage_path == tmp_path / "runtime" / "files"


def test_deepseek_provider_requires_an_api_key() -> None:
    with pytest.raises(ValidationError, match="llm_api_key is required"):
        Settings(llm_provider="deepseek", llm_api_key="")


def test_model_api_key_is_not_exposed_by_repr() -> None:
    settings = Settings(llm_provider="deepseek", llm_api_key="top-secret")

    assert "top-secret" not in repr(settings)


def test_remote_embedding_provider_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="embedding_api_key is required"):
        Settings(embedding_provider="openai_compatible", embedding_api_key="")


def test_resource_chunk_overlap_must_be_smaller_than_chunk() -> None:
    with pytest.raises(ValidationError, match="overlap must be less"):
        Settings(resource_chunk_size=200, resource_chunk_overlap=200)


def test_job_retry_window_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="job_retry_base_seconds"):
        Settings(job_retry_base_seconds=10, job_retry_max_seconds=5)
