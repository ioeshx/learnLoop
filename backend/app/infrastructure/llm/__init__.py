"""Model provider adapters and structured generation utilities."""

from app.infrastructure.llm.curriculum import LlmCurriculumGenerator
from app.infrastructure.llm.deepseek import DeepSeekModelProvider
from app.infrastructure.llm.errors import (
    ModelError,
    ModelProviderError,
    StructuredOutputError,
)
from app.infrastructure.llm.fake import FakeModelProvider, FakeOutput
from app.infrastructure.llm.models import (
    ModelCallObservation,
    ModelCallObserver,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelRouteMetadata,
    TokenUsage,
    TokenUsageTracker,
    UsageSnapshot,
)
from app.infrastructure.llm.review_exercises import LlmReviewExerciseGenerator
from app.infrastructure.llm.structured import StructuredModel, StructuredResult

__all__ = [
    "DeepSeekModelProvider",
    "FakeModelProvider",
    "FakeOutput",
    "LlmCurriculumGenerator",
    "LlmReviewExerciseGenerator",
    "ModelError",
    "ModelCallObservation",
    "ModelCallObserver",
    "ModelMessage",
    "ModelProvider",
    "ModelProviderError",
    "ModelRequest",
    "ModelResponse",
    "ModelRouteMetadata",
    "StructuredModel",
    "StructuredOutputError",
    "StructuredResult",
    "TokenUsage",
    "TokenUsageTracker",
    "UsageSnapshot",
]
