"""Study plan entities."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from app.domain.common import new_id, require_aware_utc, require_text, utc_now


class StudyPlanStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    ACTIVE = "active"
    COMPLETED = "completed"


class PlanItemStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class PlanItem:
    id: str
    plan_id: str
    knowledge_node_id: str
    title: str
    position: int
    estimated_minutes: int
    status: PlanItemStatus = PlanItemStatus.PENDING

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", require_text(self.title, "title"))
        if self.position < 0:
            raise ValueError("position must be non-negative")
        if self.estimated_minutes <= 0:
            raise ValueError("estimated_minutes must be positive")

    def change_status(self, status: PlanItemStatus) -> "PlanItem":
        return replace(self, status=status)

    @classmethod
    def create(
        cls,
        *,
        plan_id: str,
        knowledge_node_id: str,
        title: str,
        position: int,
        estimated_minutes: int,
    ) -> "PlanItem":
        return cls(
            id=new_id(),
            plan_id=require_text(plan_id, "plan_id"),
            knowledge_node_id=require_text(knowledge_node_id, "knowledge_node_id"),
            title=title,
            position=position,
            estimated_minutes=estimated_minutes,
        )


@dataclass(frozen=True, slots=True)
class StudyPlan:
    id: str
    goal_id: str
    version: int
    status: StudyPlanStatus
    created_at: datetime
    items: tuple[PlanItem, ...]

    def __post_init__(self) -> None:
        if self.version <= 0:
            raise ValueError("version must be positive")
        require_aware_utc(self.created_at, "created_at")
        positions = [item.position for item in self.items]
        if len(positions) != len(set(positions)):
            raise ValueError("plan item positions must be unique")
        if any(item.plan_id != self.id for item in self.items):
            raise ValueError("all plan items must belong to the plan")

    def get_item(self, item_id: str) -> PlanItem | None:
        return next((item for item in self.items if item.id == item_id), None)

    def update_item_status(self, item_id: str, status: PlanItemStatus) -> "StudyPlan":
        if self.get_item(item_id) is None:
            raise LookupError(f"plan item {item_id} was not found")
        return replace(
            self,
            items=tuple(
                item.change_status(status) if item.id == item_id else item
                for item in self.items
            ),
        )

    @classmethod
    def create(
        cls,
        *,
        goal_id: str,
        item_specs: list[tuple[str, str, int]],
        version: int = 1,
        now: datetime | None = None,
    ) -> "StudyPlan":
        if not item_specs:
            raise ValueError("a study plan must contain at least one item")
        plan_id = new_id()
        items = tuple(
            PlanItem.create(
                plan_id=plan_id,
                knowledge_node_id=node_id,
                title=title,
                position=position,
                estimated_minutes=estimated_minutes,
            )
            for position, (node_id, title, estimated_minutes) in enumerate(item_specs)
        )
        return cls(
            id=plan_id,
            goal_id=require_text(goal_id, "goal_id"),
            version=version,
            status=StudyPlanStatus.DRAFT,
            created_at=require_aware_utc(now or utc_now(), "now"),
            items=items,
        )
