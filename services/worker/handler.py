"""The worker: turns one queued PR event into a pipeline run (screen, triage, proposal).

Reads GitHub as the read-only App when it is configured (GITHUB_READER_APP_ID; its own
installation on the repo is looked up once and cached), otherwise as the main App's
installation named in the event (local). With REPLAY_BUNDLE set, the historical PRs in that
file are served from it as of their open time (ReplayGitHubClient), for the pilot and the
shadow replay; every other PR is read live as before. The student model is called through
llama.cpp's OpenAI-compatible server (MODEL_BASE_URL). The screening models load once per process.
"""

from functools import cache

import httpx
from opentelemetry import trace
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from prpilot_core.gcp import CloudRunIdToken
from prpilot_core.github.live import AppAuth, LiveGitHubClient, installation_for
from prpilot_core.github.replay import ReplayGitHubClient
from prpilot_core.pipeline import TRACER_NAME, Pipeline, should_retry
from prpilot_core.settings import Settings


@cache
def screening(prompt_guard_dir: str | None, hf_token: str | None, pii: bool):
    from prpilot_core.screening.pii import PIIRedactor
    from prpilot_core.screening.prompt_guard import PROMPT_GUARD_86M, PromptGuard

    guard = PromptGuard(PROMPT_GUARD_86M, token=hf_token, local_dir=prompt_guard_dir)
    return guard, (PIIRedactor() if pii else None)


def make_handler(settings: Settings, sessions, tracer_provider=None):
    """`tracer_provider`: the one tracing set up (prpilot_core.tracing), so the pipeline's step
    spans and the model spans land in the same trace; None means no spans."""
    http = httpx.AsyncClient(timeout=30)
    tracer = (tracer_provider or trace.get_tracer_provider()).get_tracer(TRACER_NAME)
    # On Cloud Run the model service accepts only the worker's identity token; locally
    # (MODEL_AUDIENCE unset) llama.cpp on localhost needs none.
    model_http = (
        httpx.AsyncClient(timeout=600, auth=CloudRunIdToken(settings.model_audience))
        if settings.model_audience
        else None
    )
    model = OpenAIChatModel(
        settings.model_name,
        provider=OpenAIProvider(
            base_url=settings.model_base_url, api_key="local", http_client=model_http
        ),
    )
    reader_installations: dict[str, int] = {}
    replay = (
        ReplayGitHubClient.from_bundle(settings.replay_bundle) if settings.replay_bundle else None
    )

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
        replayed = replay is not None and (event["repo"], event["number"]) in replay
        if not replayed and event["repo"] not in settings.sandbox_repos:
            return False  # only sandbox repos (and replayed history) are triaged
        guard, pii = screening(settings.prompt_guard_dir, settings.hf_token, settings.pii)
        gh = replay if replayed else LiveGitHubClient(auth=await read_auth(event), http=http)
        pipeline = Pipeline(sessions=sessions, github=gh, guard=guard, model=model, pii=pii,
                            tracer=tracer)  # fmt: skip
        return should_retry(await pipeline.run(event["repo"], event["number"]))

    return handle
