"""Cloud Run `executor`: runs one approved action through the lock and writes it to GitHub.

    uvicorn services.executor.main:app --port 8080

`POST /proposals/{id}/actions/{action}/execute` with the admin token. This is the only service
holding the writing App's key, and it writes only to SANDBOX_REPOS; EXECUTOR_DRY_RUN (default
true) records the execution without writing. Settings: DATABASE_URL, ADMIN_TOKEN,
SANDBOX_REPOS, EXECUTOR_DRY_RUN, GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY_PATH.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from prpilot_core.db.session import make_engine, make_sessionmaker
from prpilot_core.github.live import AppWriter
from prpilot_core.settings import Settings
from services.api.app import Services, create_app

settings = Settings.from_env()
if not settings.dry_run and not settings.app_id:
    raise RuntimeError("EXECUTOR_DRY_RUN=false needs GITHUB_APP_ID and its private key")
engine = make_engine(settings.database_url)
app = create_app(
    Services(
        sessions=make_sessionmaker(engine),
        queue=None,
        webhook_secret="",
        admin_token=settings.admin_token,
        writer=AppWriter(settings.app_id, settings.app_private_key) if settings.app_id else None,
        allowlist=settings.sandbox_repos,
        dry_run=settings.dry_run,
    ),
    parts=("execute",),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()


app.router.lifespan_context = lifespan
