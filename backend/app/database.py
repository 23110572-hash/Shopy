"""Async PostgreSQL lifecycle and session management."""

import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.config import Settings


class DatabaseUnavailable(RuntimeError):
    """Sanitized database readiness failure."""


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            settings.sqlalchemy_database_url,
            echo=settings.sql_echo,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            connect_args={
                "ssl": ssl.create_default_context(),
                "statement_cache_size": 0,
            },
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def ping(self) -> None:
        try:
            async with self.engine.connect() as connection:
                value = await connection.scalar(text("SELECT 1"))
        except Exception:
            raise DatabaseUnavailable("Database readiness check failed") from None
        if value != 1:
            raise DatabaseUnavailable("Database readiness check failed")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
