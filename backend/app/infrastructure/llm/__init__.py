"""Model provider adapters and structured generation utilities."""

from app.infrastructure.llm.deepseek import DeepSeekModelProvider
from app.infrastructure.llm.errors import (
    ModelError,
    ModelProviderError,
    StructuredOutputError,
)
from app.infrastructure.llm.fake import FakeModelProvider, FakeOutput
from app.infrastructure.llm.models import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    TokenUsage,
    TokenUsageTracker,
    UsageSnapshot,
)
from app.infrastructure.llm.structured import StructuredModel, StructuredResult

__all__ = [
    "DeepSeekModelProvider",
    "FakeModelProvider",
    "FakeOutput",
    "ModelError",
    "ModelMessage",
    "ModelProvider",
    "ModelProviderError",
    "ModelRequest",
    "ModelResponse",
    "StructuredModel",
    "StructuredOutputError",
    "StructuredResult",
    "TokenUsage",
    "TokenUsageTracker",
    "UsageSnapshot",
]
