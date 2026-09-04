"""Local user entity."""

from dataclasses import dataclass
from datetime import datetime

from app.domain.common import new_id, require_aware_utc, require_text, utc_now


@dataclass(frozen=True, slots=True)
class User:
    id: str
    display_name: str
    timezone: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "display_name", require_text(self.display_name, "display_name")
        )
        object.__setattr__(self, "timezone", require_text(self.timezone, "timezone"))
        require_aware_utc(self.created_at, "created_at")
        require_aware_utc(self.updated_at, "updated_at")

    @classmethod
    def create(
        cls,
        *,
        display_name: str,
        timezone_name: str,
        now: datetime | None = None,
    ) -> "User":
        created_at = require_aware_utc(now or utc_now(), "now")
        return cls(
            id=new_id(),
            display_name=display_name,
            timezone=timezone_name,
            created_at=created_at,
            updated_at=created_at,
        )
