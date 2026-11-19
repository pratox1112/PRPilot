"""Service settings, all from environment variables (never from code; secrets from .env
locally and from Secret Manager in Phase 6)."""

import os
from dataclasses import dataclass
from pathlib import Path


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _read_key(name: str) -> str:
    """A private key from the file the variable names (a Secret Manager mount on Cloud Run)."""
    path = os.environ.get(name, "")
    return Path(path).expanduser().read_text() if path else ""


@dataclass(frozen=True)
class Settings:
    database_url: str
    webhook_secret: str
    admin_token: str
    sandbox_repos: frozenset[str]
    dry_run: bool
    app_id: str
    app_private_key: str
    reader_app_id: str
    reader_app_private_key: str
    gcp_project: str
    pubsub_topic: str
    model_base_url: str
    model_name: str
    model_audience: str
    hf_token: str | None
    prompt_guard_dir: str | None
    pii: bool
    tracing: bool
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str
    replay_bundle: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            # Stripped: a secret stored with a trailing newline would fail every comparison.
            webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET", "").strip(),
            admin_token=os.environ.get("ADMIN_TOKEN", "").strip(),
            sandbox_repos=frozenset(
                r.strip() for r in os.environ.get("SANDBOX_REPOS", "").split(",") if r.strip()
            ),
            # Writes are off unless explicitly turned on.
            dry_run=_flag("EXECUTOR_DRY_RUN", True),
            app_id=os.environ.get("GITHUB_APP_ID", ""),
            app_private_key=_read_key("GITHUB_APP_PRIVATE_KEY_PATH"),
            # The worker reads PRs as a second, read-only App, so the service that sees
            # untrusted text holds no key that can write. Unset locally: the main App reads.
            reader_app_id=os.environ.get("GITHUB_READER_APP_ID", ""),
            reader_app_private_key=_read_key("GITHUB_READER_APP_PRIVATE_KEY_PATH"),
            gcp_project=os.environ.get("GCP_PROJECT", ""),
            pubsub_topic=os.environ.get("PUBSUB_TOPIC", ""),
            model_base_url=os.environ.get("MODEL_BASE_URL", "http://127.0.0.1:8080/v1"),
            model_name=os.environ.get("MODEL_NAME", "prpilot-model"),
            # Cloud Run: the model service's URL, the audience of the worker's identity token.
            model_audience=os.environ.get("MODEL_AUDIENCE", ""),
            hf_token=os.environ.get("HF_TOKEN") or None,
            prompt_guard_dir=os.environ.get("PROMPT_GUARD_DIR") or None,
            pii=_flag("SCREEN_PII", True),
            # Off unless explicitly turned on (spans go to Langfuse, an outside service).
            tracing=_flag("TRACING", False),
            langfuse_public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
            langfuse_secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
            langfuse_host=os.environ.get("LANGFUSE_HOST", ""),
            # Shadow replay / pilot: historical PRs the worker serves from this file instead
            # of GitHub (prpilot_core.github.replay); unset in normal operation.
            replay_bundle=os.environ.get("REPLAY_BUNDLE", ""),
        )
