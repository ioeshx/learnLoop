"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed LearnLoop settings.

    Environment variables use the ``LEARNLOOP_`` prefix. A root ``.env`` file
    is supported for local development, while real environment variables take
    precedence.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="LEARNLOOP_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "LearnLoop API"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = PROJECT_ROOT / "data"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:3000", "http://localhost:3000"]
    )
    llm_provider: Literal["none", "deepseek"] = "none"
    llm_model: str = "deepseek-v4-flash"
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://api.deepseek.com"
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    checkpoint_retention_days: int = Field(default=30, ge=1, le=3650)

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("api_prefix must start with '/'")
        return value.rstrip("/")

    @field_validator("data_dir", mode="before")
    @classmethod
    def reject_empty_data_dir(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("data_dir must not be empty")
        return value

    @field_validator("data_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return (PROJECT_ROOT / value).resolve()

    @field_validator("llm_model")
    @classmethod
    def validate_llm_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("llm_model must not be empty")
        return normalized

    @field_validator("llm_base_url")
    @classmethod
    def validate_llm_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("https://", "http://")):
            raise ValueError("llm_base_url must be an HTTP(S) URL")
        return normalized

    @model_validator(mode="after")
    def validate_llm_credentials(self) -> "Settings":
        if self.llm_provider == "deepseek" and (
            self.llm_api_key is None or not self.llm_api_key.get_secret_value().strip()
        ):
            raise ValueError("llm_api_key is required when llm_provider is 'deepseek'")
        return self

    @property
    def database_dir(self) -> Path:
        return self.data_dir / "db"

    @property
    def database_path(self) -> Path:
        return self.database_dir / "learnloop.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path.as_posix()}"

    @property
    def checkpoint_path(self) -> Path:
        return self.database_dir / "checkpoints.db"

    def ensure_runtime_directories(self) -> None:
        """Create only the runtime directories required at application boot."""
        self.database_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()
