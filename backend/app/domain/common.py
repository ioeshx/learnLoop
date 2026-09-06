"""Shared domain primitives with no framework dependencies."""

from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

LEARNLOOP_NAMESPACE = UUID("a3a66a77-d569-4ff8-97b7-8e56c060f294")


def new_id() -> str:
    return str(uuid4())


def deterministic_id(scope: str, idempotency_key: str) -> str:
    """Create a stable UUID for an operation-scoped idempotency key."""
    normalized_scope = require_text(scope, "scope")
    normalized_key = require_text(idempotency_key, "idempotency_key")
    return str(uuid5(LEARNLOOP_NAMESPACE, f"{normalized_scope}:{normalized_key}"))


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_aware_utc(value: datetime, field_name: str = "datetime") -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized
