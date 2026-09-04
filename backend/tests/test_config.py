"""Configuration validation tests."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_api_prefix_must_start_with_slash() -> None:
    with pytest.raises(ValidationError, match="api_prefix must start"):
        Settings(api_prefix="api/v1")


def test_port_has_clear_validation_error() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 65535"):
        Settings(port=70_000)
