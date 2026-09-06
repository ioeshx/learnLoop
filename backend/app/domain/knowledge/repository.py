"""Knowledge graph persistence contract."""

from typing import Protocol

from app.domain.knowledge.models import KnowledgeEdge, KnowledgeNode


class KnowledgeRepository(Protocol):
    async def add_node(self, node: KnowledgeNode) -> None: ...

    async def add_edge(self, edge: KnowledgeEdge) -> None: ...

    async def get_node(self, node_id: str) -> KnowledgeNode | None: ...

    async def list_nodes(self, goal_id: str) -> list[KnowledgeNode]: ...

    async def list_edges(self, goal_id: str) -> list[KnowledgeEdge]: ...
