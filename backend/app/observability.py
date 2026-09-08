"""Context propagated through an Agent run without entering graph state."""

from contextvars import ContextVar, Token

_agent_run_id: ContextVar[str | None] = ContextVar("agent_run_id", default=None)


def current_agent_run_id() -> str | None:
    return _agent_run_id.get()


def bind_agent_run(run_id: str) -> Token[str | None]:
    return _agent_run_id.set(run_id)


def reset_agent_run(token: Token[str | None]) -> None:
    _agent_run_id.reset(token)
