"""Knowledge graph entities."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.common import new_id, require_aware_utc, require_text, utc_now


class RelationType(StrEnum):
    PREREQUISITE = "prerequisite"
    PART_OF = "part_of"
    RELATED = "related"


@dataclass(frozen=True, slots=True)
class KnowledgeNode:
    id: str
    goal_id: str
    title: str
    description: str
    difficulty: float
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", require_text(self.title, "title"))
        if not 1.0 <= self.difficulty <= 5.0:
            raise ValueError("difficulty must be between 1.0 and 5.0")
        require_aware_utc(self.created_at, "created_at")

    @classmethod
    def create(
        cls,
        *,
        goal_id: str,
        title: str,
        description: str = "",
        difficulty: float = 1.0,
        now: datetime | None = None,
    ) -> "KnowledgeNode":
        return cls(
            id=new_id(),
            goal_id=require_text(goal_id, "goal_id"),
            title=title,
            description=description.strip(),
            difficulty=difficulty,
            created_at=require_aware_utc(now or utc_now(), "now"),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeEdge:
    id: str
    goal_id: str
    source_node_id: str
    target_node_id: str
    relation: RelationType
    created_at: datetime

    def __post_init__(self) -> None:
        if self.source_node_id == self.target_node_id:
            raise ValueError("a knowledge edge cannot point to itself")
        require_aware_utc(self.created_at, "created_at")

    @classmethod
    def create(
        cls,
        *,
        goal_id: str,
        source_node_id: str,
        target_node_id: str,
        relation: RelationType = RelationType.PREREQUISITE,
        now: datetime | None = None,
    ) -> "KnowledgeEdge":
        return cls(
            id=new_id(),
            goal_id=require_text(goal_id, "goal_id"),
            source_node_id=require_text(source_node_id, "source_node_id"),
            target_node_id=require_text(target_node_id, "target_node_id"),
            relation=relation,
            created_at=require_aware_utc(now or utc_now(), "now"),
        )
