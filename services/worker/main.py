"""Cloud Run `worker`: screens and triages the PR events Pub/Sub pushes to it.

    uvicorn services.worker.main:app --port 8080

Settings from the environment: DATABASE_URL, SANDBOX_REPOS, GITHUB_READER_APP_ID and
GITHUB_READER_APP_PRIVATE_KEY_PATH (the read-only App; no key that can write is mounted
here), MODEL_BASE_URL and MODEL_NAME (the `model` service), PROMPT_GUARD_DIR or HF_TOKEN,
SCREEN_PII, and TRACING with the LANGFUSE_* keys.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from prpilot_core.db.session import make_engine, make_sessionmaker
from prpilot_core.settings import Settings
from prpilot_core.tracing import setup_tracing
from services.worker.handler import make_handler
from services.worker.push import create_worker_app

settings = Settings.from_env()
tracer_provider = setup_tracing(settings)
engine = make_engine(settings.database_url)
app = create_worker_app(make_handler(settings, make_sessionmaker(engine)))


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()
    if tracer_provider is not None:
        tracer_provider.shutdown()  # sends the spans still buffered


app.router.lifespan_context = lifespan
