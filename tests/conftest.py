import os

import pytest

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

TEST_DB_ENV = "PRPILOT_TEST_DATABASE_URL"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    """A fresh schema in the real test Postgres (docker compose `db`, or the CI service).

    Skips when PRPILOT_TEST_DATABASE_URL is unset (CI always sets it). Each test gets empty
    tables: the schema is dropped and created from the models, so tests are independent.
    """
    url = os.environ.get(TEST_DB_ENV)
    if not url:
        pytest.skip(f"{TEST_DB_ENV} not set (start Postgres: docker compose up -d db)")
    from prpilot_core.db.models import Base
    from prpilot_core.db.session import make_engine, make_sessionmaker

    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()
