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
    return create_async_engine(url or database_url(), pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
