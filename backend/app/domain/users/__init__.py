"""Local user domain."""

from app.domain.users.models import User
from app.domain.users.repository import UserRepository

__all__ = ["User", "UserRepository"]
