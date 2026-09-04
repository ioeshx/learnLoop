"""Errors raised by deterministic domain rules."""


class DomainError(ValueError):
    """Base class for invalid domain operations."""


class KnowledgeGraphError(DomainError):
    """The proposed knowledge graph violates a structural invariant."""


class UnsupportedExerciseError(DomainError):
    """The requested deterministic grader does not support the exercise type."""
