"""Stable errors raised by the model infrastructure boundary."""


class ModelError(Exception):
    """Base class for errors callers may safely handle."""


class ModelProviderError(ModelError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code


class StructuredOutputError(ModelError):
    def __init__(self, *, prompt_name: str, attempts: int, reason: str) -> None:
        super().__init__(
            f"model output for '{prompt_name}' remained invalid after "
            f"{attempts} attempt(s): {reason}"
        )
        self.prompt_name = prompt_name
        self.attempts = attempts
        self.reason = reason
