"""Cloud Run `api`: the GitHub webhook and the approval API (runs, proposals, decisions).

    uvicorn services.api.main:app --port 8080

Settings from the environment: DATABASE_URL, GITHUB_WEBHOOK_SECRET, ADMIN_TOKEN, GCP_PROJECT
and PUBSUB_TOPIC. No GitHub key is mounted here: executing approved actions is the separate
`executor` service.
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from prpilot_core.db.session import make_engine, make_sessionmaker
from prpilot_core.queue import PubSubQueue
from prpilot_core.settings import Settings
from services.api.app import Services, create_app

settings = Settings.from_env()
engine = make_engine(settings.database_url)
http = httpx.AsyncClient(timeout=10)
app = create_app(
    Services(
        sessions=make_sessionmaker(engine),
        queue=PubSubQueue(settings.gcp_project, settings.pubsub_topic, http),
        webhook_secret=settings.webhook_secret,
        admin_token=settings.admin_token,
        writer=None,
        allowlist=settings.sandbox_repos,
        dry_run=settings.dry_run,
    ),
    parts=("webhook", "review"),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await http.aclose()
    await engine.dispose()


app.router.lifespan_context = lifespan
