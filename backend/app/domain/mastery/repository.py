"""Mastery persistence contract."""

from typing import Protocol

from app.domain.mastery.models import MasteryEvent, MasterySnapshot


class MasteryRepository(Protocol):
    async def add_event(self, event: MasteryEvent) -> None: ...

    async def list_events(
        self, user_id: str, knowledge_node_id: str
    ) -> list[MasteryEvent]:
        """按稳定顺序返回知识点的全部事件，供掌握度投影重放。"""
        ...

    async def update_event(self, event: MasteryEvent) -> None:
        """替换已有事件的类型和增量，使作答纠正能保留事件身份。"""
        ...

    async def get_snapshot(
        self, user_id: str, knowledge_node_id: str
    ) -> MasterySnapshot | None: ...

    async def save_snapshot(self, snapshot: MasterySnapshot) -> None: ...
