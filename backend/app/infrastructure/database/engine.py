"""Async SQLite engine and session factory."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


class DbApiCursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def close(self) -> None: ...


class DbApiConnection(Protocol):
    def cursor(self) -> DbApiCursor: ...


def configure_sqlite_connection(dbapi_connection: DbApiConnection, _: object) -> None:
    """Apply invariants to a SQLite connection owned by this application."""
    # SQLAlchemy wraps the native connection when the aiosqlite driver is used,
    # but deliberately exposes the standard DB-API cursor contract here.
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 5000")
    finally:
        cursor.close()


@dataclass(frozen=True, slots=True)
class Database:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    async def dispose(self) -> None:
        await self.engine.dispose()


def create_database(settings: Settings, *, echo: bool = False) -> Database:
    settings.ensure_runtime_directories()
    engine = create_async_engine(settings.database_url, echo=echo)
    event.listen(engine.sync_engine, "connect", configure_sqlite_connection)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return Database(engine=engine, session_factory=session_factory)
