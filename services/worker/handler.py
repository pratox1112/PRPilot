"""The worker: turns one queued PR event into a pipeline run (screen, triage, proposal).

Reads GitHub as the read-only App when it is configured (GITHUB_READER_APP_ID; its own
installation on the repo is looked up once and cached), otherwise as the main App's
installation named in the event (local). The student model is called through llama.cpp's
OpenAI-compatible server (MODEL_BASE_URL). The screening models load once per process.
"""

from functools import cache

import httpx
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from prpilot_core.github.live import AppAuth, LiveGitHubClient, installation_for
from prpilot_core.pipeline import Pipeline, should_retry
from prpilot_core.settings import Settings


@cache
def screening(prompt_guard_dir: str | None, hf_token: str | None, pii: bool):
    from prpilot_core.screening.pii import PIIRedactor
    from prpilot_core.screening.prompt_guard import PROMPT_GUARD_86M, PromptGuard

    guard = PromptGuard(PROMPT_GUARD_86M, token=hf_token, local_dir=prompt_guard_dir)
    return guard, (PIIRedactor() if pii else None)


def make_handler(settings: Settings, sessions):
    http = httpx.AsyncClient(timeout=30)
    model = OpenAIChatModel(
        settings.model_name,
        provider=OpenAIProvider(base_url=settings.model_base_url, api_key="local"),
    )
    reader_installations: dict[str, int] = {}

    async def read_auth(event: dict) -> AppAuth:
        if not settings.reader_app_id:
            return AppAuth(settings.app_id, settings.app_private_key, event["installation_id"])
        repo = event["repo"]
        if repo not in reader_installations:
            reader_installations[repo] = await installation_for(
                settings.reader_app_id, settings.reader_app_private_key, repo, http
            )
        return AppAuth(
            settings.reader_app_id, settings.reader_app_private_key, reader_installations[repo]
        )

    async def handle(event: dict) -> bool:
        """Run the pipeline; True if the queue should deliver the event again."""
        if event["repo"] not in settings.sandbox_repos:
            return False  # only sandbox repos are triaged
        guard, pii = screening(settings.prompt_guard_dir, settings.hf_token, settings.pii)
        gh = LiveGitHubClient(auth=await read_auth(event), http=http)
        pipeline = Pipeline(sessions=sessions, github=gh, guard=guard, model=model, pii=pii)
        return should_retry(await pipeline.run(event["repo"], event["number"]))

    return handle
