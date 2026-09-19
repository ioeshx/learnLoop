"""Per-provider closed/open/half-open circuit breaker state machine."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.infrastructure.llm.gateway.models import CircuitState, ProviderHealth


class CircuitBreakerPool:
    def __init__(
        self,
        provider_ids: list[str],
        *,
        failure_threshold: int = 3,
        recovery_seconds: float = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if recovery_seconds <= 0:
            raise ValueError("recovery_seconds must be positive")
        self.failure_threshold = failure_threshold
        self.recovery = timedelta(seconds=recovery_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._health = {
            provider_id: ProviderHealth(provider_id=provider_id)
            for provider_id in provider_ids
        }

    def unavailable(self) -> set[str]:
        now = self._clock()
        return {
            provider_id
            for provider_id, health in self._health.items()
            if not _can_attempt(health, now, self.recovery)
        }

    def acquire(self, provider_id: str) -> bool:
        health = self._health[provider_id]
        now = self._clock()
        if health.state == CircuitState.CLOSED:
            return True
        if health.state == CircuitState.OPEN:
            if not _recovery_elapsed(health, now, self.recovery):
                return False
            self._health[provider_id] = health.model_copy(
                update={
                    "state": CircuitState.HALF_OPEN,
                    "half_open_probe_in_flight": True,
                }
            )
            return True
        if health.half_open_probe_in_flight:
            return False
        self._health[provider_id] = health.model_copy(
            update={"half_open_probe_in_flight": True}
        )
        return True

    def record_success(self, provider_id: str) -> None:
        self._health[provider_id] = ProviderHealth(provider_id=provider_id)

    def record_failure(self, provider_id: str, *, retryable: bool) -> None:
        health = self._health[provider_id]
        if not retryable:
            # Authentication/validation failures must not trigger fallback. They
            # also do not represent transient service health for the breaker.
            if health.state == CircuitState.HALF_OPEN:
                self._health[provider_id] = ProviderHealth(provider_id=provider_id)
            return
        failures = health.consecutive_failures + 1
        should_open = (
            health.state == CircuitState.HALF_OPEN
            or failures >= self.failure_threshold
        )
        self._health[provider_id] = health.model_copy(
            update={
                "state": CircuitState.OPEN if should_open else CircuitState.CLOSED,
                "consecutive_failures": failures,
                "opened_at": self._clock() if should_open else None,
                "half_open_probe_in_flight": False,
            }
        )

    def snapshots(self) -> list[ProviderHealth]:
        return [item.model_copy(deep=True) for item in self._health.values()]


def _can_attempt(
    health: ProviderHealth, now: datetime, recovery: timedelta
) -> bool:
    if health.state == CircuitState.CLOSED:
        return True
    if health.state == CircuitState.HALF_OPEN:
        return not health.half_open_probe_in_flight
    return _recovery_elapsed(health, now, recovery)


def _recovery_elapsed(
    health: ProviderHealth, now: datetime, recovery: timedelta
) -> bool:
    return health.opened_at is not None and now - health.opened_at >= recovery
