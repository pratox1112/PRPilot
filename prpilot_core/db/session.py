"""Async engine and session factory. The URL comes from DATABASE_URL (never hard-coded):
postgresql+asyncpg://user:password@host:port/db locally, a Cloud SQL socket URL in Phase 6."""

import os

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def make_engine(url: str | None = None) -> AsyncEngine:
    """Pool bounds from DB_POOL_SIZE and DB_MAX_OVERFLOW (SQLAlchemy's 5 and 10 if unset). On
    Cloud Run each service sets them, so all instances together stay under the database's
    connection limit."""
    return create_async_engine(
        url or database_url(),
        pool_pre_ping=True,
        pool_size=int(os.environ.get("DB_POOL_SIZE", "5")),
        max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "10")),
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
