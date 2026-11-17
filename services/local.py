"""Everything in one process for local development and the sandbox end-to-end run:
the API (webhook + approval API), the in-process queue, the worker, and the executor's
GitHub writer. Phase 6 runs the same pieces as separate Cloud Run services with Pub/Sub.

    uv run --group screening uvicorn services.local:app --port 8000

Settings come from the environment (.env): DATABASE_URL, GITHUB_WEBHOOK_SECRET, ADMIN_TOKEN,
SANDBOX_REPOS, EXECUTOR_DRY_RUN (default true: nothing is written to GitHub), GITHUB_APP_ID,
GITHUB_APP_PRIVATE_KEY_PATH, MODEL_BASE_URL, HF_TOKEN or PROMPT_GUARD_DIR.
"""

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from prpilot_core.db.session import make_engine, make_sessionmaker
from prpilot_core.github.live import AppWriter
from prpilot_core.queue import InProcessQueue
from prpilot_core.settings import Settings
from services.api.app import Services, create_app
from services.worker.handler import make_handler

load_dotenv()
settings = Settings.from_env()
engine = make_engine(settings.database_url)
sessions = make_sessionmaker(engine)
queue = InProcessQueue(make_handler(settings, sessions))
writer = AppWriter(settings.app_id, settings.app_private_key) if settings.app_id else None
api = create_app(
    Services(
        sessions=sessions,
        queue=queue,
        webhook_secret=settings.webhook_secret,
        admin_token=settings.admin_token,
        writer=writer,
        allowlist=settings.sandbox_repos,
        dry_run=settings.dry_run,
    )
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    queue.start()
    yield
    await queue.stop()
    await engine.dispose()


api.router.lifespan_context = lifespan
app = api
