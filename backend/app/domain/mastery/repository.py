"""Mastery persistence contract."""

from typing import Protocol

from app.domain.mastery.models import MasteryEvent, MasterySnapshot


class MasteryRepository(Protocol):
    async def add_event(self, event: MasteryEvent) -> None: ...

    async def list_events(
        self, user_id: str, knowledge_node_id: str
    ) -> list[MasteryEvent]: ...

    async def update_event(self, event: MasteryEvent) -> None: ...

    async def get_snapshot(
        self, user_id: str, knowledge_node_id: str
    ) -> MasterySnapshot | None: ...

    async def save_snapshot(self, snapshot: MasterySnapshot) -> None: ...
