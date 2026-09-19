"""Trusted Role Registry for general Agent Team adapters."""

from __future__ import annotations

from typing import Protocol

from app.agent.team.models import AgentCard, ArtifactDraft, TeamTask


class RoleAdapter(Protocol):
    @property
    def card(self) -> AgentCard: ...

    async def execute(self, task: TeamTask) -> ArtifactDraft: ...


class RoleRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, RoleAdapter] = {}

    def register(self, adapter: RoleAdapter) -> None:
        role_id = adapter.card.role_id
        if role_id in self._adapters:
            raise ValueError(f"Role '{role_id}' is already registered")
        self._adapters[role_id] = adapter

    def get(self, role_id: str) -> RoleAdapter | None:
        return self._adapters.get(role_id)

    def cards(self) -> list[AgentCard]:
        return [item.card for item in self._adapters.values()]
