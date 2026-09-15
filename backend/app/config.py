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
    agent_dynamic_writes_enabled: bool = False
    agent_max_steps: int = Field(default=24, ge=2, le=200)
    agent_max_model_calls: int = Field(default=30, ge=1, le=200)
    agent_max_tool_calls: int = Field(default=20, ge=0, le=200)
    agent_max_input_tokens: int = Field(default=60_000, ge=1_000)
    agent_max_output_tokens: int = Field(default=20_000, ge=500)
    agent_max_total_tokens: int = Field(default=80_000, ge=1_500)
    agent_deadline_seconds: float = Field(default=300.0, gt=0, le=3_600)
    agent_max_same_action: int = Field(default=2, ge=1, le=10)
    agent_max_consecutive_failures: int = Field(default=3, ge=1, le=20)
    agent_max_replans: int = Field(default=2, ge=0, le=10)
    agent_context_tokens: int = Field(default=12_000, ge=1_000, le=200_000)
    agent_context_output_reserve_tokens: int = Field(default=2_048, ge=128)
    agent_context_recent_observations: int = Field(default=24, ge=1, le=100)
    agent_context_source_ttl_seconds: int = Field(
        default=86_400, ge=60, le=31_536_000
    )
    agent_context_debug_full: bool = False
    agent_memory_enabled: bool = True
    agent_memory_recall_limit: int = Field(default=6, ge=1, le=20)
    agent_memory_minimum_score: float = Field(default=0.24, ge=0, le=1)
    research_max_rounds: int = Field(default=3, ge=1, le=8)
    research_max_queries: int = Field(default=8, ge=1, le=30)
    research_max_sources: int = Field(default=12, ge=1, le=50)
    research_max_read_chars: int = Field(default=30_000, ge=1_000, le=200_000)
    research_max_context_tokens: int = Field(default=8_000, ge=500, le=50_000)
    agent_max_subagents: int = Field(default=3, ge=1, le=12)
    agent_delegation_max_tokens: int = Field(default=6_000, ge=500, le=50_000)
    agent_delegation_max_queries: int = Field(default=6, ge=1, le=30)
    agent_delegation_max_sources: int = Field(default=10, ge=1, le=50)
    agent_delegation_deadline_seconds: float = Field(default=60, gt=0, le=600)
    agent_skill_library_enabled: bool = True
    agent_skill_admin_enabled: bool = True
    agent_skill_minimum_source_runs: int = Field(default=2, ge=2, le=20)
    agent_skill_recall_limit: int = Field(default=3, ge=1, le=10)
    agent_skill_quarantine_min_uses: int = Field(default=3, ge=1, le=100)
    agent_skill_quarantine_success_rate: float = Field(default=0.5, ge=0, le=1)
    agent_policy_optimization_enabled: bool = False
    agent_policy_admin_enabled: bool = True
    agent_policy_expected_latency_ms: float = Field(default=30_000, gt=0)
    embedding_provider: Literal["local", "openai_compatible"] = "local"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: SecretStr | None = None
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_dimensions: int = Field(default=384, ge=32, le=4096)
    resource_max_bytes: int = Field(
        default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024
    )
    resource_chunk_size: int = Field(default=1000, ge=100, le=8000)
    resource_chunk_overlap: int = Field(default=150, ge=0, le=2000)
    web_fetch_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    worker_poll_interval_seconds: float = Field(default=0.5, gt=0, le=60)
    worker_lease_seconds: float = Field(default=60.0, ge=5, le=3600)
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_retry_base_seconds: float = Field(default=2.0, ge=0, le=3600)
    job_retry_max_seconds: float = Field(default=60.0, ge=0, le=86400)

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

    @field_validator("embedding_model")
    @classmethod
    def validate_embedding_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("embedding_model must not be empty")
        return normalized

    @field_validator("embedding_base_url")
    @classmethod
    def validate_embedding_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("https://", "http://")):
            raise ValueError("embedding_base_url must be an HTTP(S) URL")
        return normalized

    @model_validator(mode="after")
    def validate_llm_credentials(self) -> "Settings":
        if self.llm_provider == "deepseek" and (
            self.llm_api_key is None or not self.llm_api_key.get_secret_value().strip()
        ):
            raise ValueError("llm_api_key is required when llm_provider is 'deepseek'")
        if self.embedding_provider == "openai_compatible" and (
            self.embedding_api_key is None
            or not self.embedding_api_key.get_secret_value().strip()
        ):
            raise ValueError(
                "embedding_api_key is required when embedding_provider is "
                "'openai_compatible'"
            )
        if self.resource_chunk_overlap >= self.resource_chunk_size:
            raise ValueError("resource_chunk_overlap must be less than chunk size")
        if self.job_retry_base_seconds > self.job_retry_max_seconds:
            raise ValueError(
                "job_retry_base_seconds must not exceed job_retry_max_seconds"
            )
        if self.agent_max_total_tokens > (
            self.agent_max_input_tokens + self.agent_max_output_tokens
        ):
            raise ValueError(
                "agent_max_total_tokens cannot exceed input plus output limits"
            )
        if self.agent_context_output_reserve_tokens >= self.agent_context_tokens:
            raise ValueError(
                "agent_context_output_reserve_tokens must be less than "
                "agent_context_tokens"
            )
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

    @property
    def document_storage_path(self) -> Path:
        return self.data_dir / "files"

    def ensure_runtime_directories(self) -> None:
        """Create only the runtime directories required at application boot."""
        self.database_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable settings instance."""
    return Settings()
