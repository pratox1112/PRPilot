"""The worker: turns one queued PR event into a pipeline run (screen, triage, proposal).

Reads GitHub as the App's installation named in the event; the student model is called
through llama.cpp's OpenAI-compatible server (MODEL_BASE_URL). The screening models load once
per process.
"""

from functools import cache

import httpx
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from prpilot_core.github.live import AppAuth, LiveGitHubClient
from prpilot_core.pipeline import Pipeline
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

    async def handle(event: dict) -> None:
        if event["repo"] not in settings.sandbox_repos:
            return  # only sandbox repos are triaged
        guard, pii = screening(settings.prompt_guard_dir, settings.hf_token, settings.pii)
        gh = LiveGitHubClient(
            auth=AppAuth(settings.app_id, settings.app_private_key, event["installation_id"]),
            http=http,
        )
        pipeline = Pipeline(sessions=sessions, github=gh, guard=guard, model=model, pii=pii)
        await pipeline.run(event["repo"], event["number"])

    return handle
