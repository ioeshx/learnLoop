"""User persistence contract."""

from typing import Protocol

from app.domain.users.models import User


class UserRepository(Protocol):
    async def add(self, user: User) -> None: ...

    async def get(self, user_id: str) -> User | None: ...
