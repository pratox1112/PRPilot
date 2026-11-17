"""Alembic environment: async engine from DATABASE_URL; target is prpilot_core.db.models."""

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from prpilot_core.db.models import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=os.environ["DATABASE_URL"], target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
