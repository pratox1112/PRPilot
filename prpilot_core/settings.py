"""Service settings, all from environment variables (never from code; secrets from .env
locally and from Secret Manager in Phase 6)."""

import os
from dataclasses import dataclass
from pathlib import Path


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str
    webhook_secret: str
    admin_token: str
    sandbox_repos: frozenset[str]
    dry_run: bool
    app_id: str
    app_private_key: str
    model_base_url: str
    model_name: str
    hf_token: str | None
    prompt_guard_dir: str | None
    pii: bool
    tracing: bool
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str

    @classmethod
    def from_env(cls) -> "Settings":
        key_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")
        return cls(
            database_url=os.environ.get("DATABASE_URL", ""),
            webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
            admin_token=os.environ.get("ADMIN_TOKEN", ""),
            sandbox_repos=frozenset(
                r.strip() for r in os.environ.get("SANDBOX_REPOS", "").split(",") if r.strip()
            ),
            # Writes are off unless explicitly turned on.
            dry_run=_flag("EXECUTOR_DRY_RUN", True),
            app_id=os.environ.get("GITHUB_APP_ID", ""),
            app_private_key=Path(key_path).expanduser().read_text() if key_path else "",
            model_base_url=os.environ.get("MODEL_BASE_URL", "http://127.0.0.1:8080/v1"),
            model_name=os.environ.get("MODEL_NAME", "prpilot-model"),
            hf_token=os.environ.get("HF_TOKEN") or None,
            prompt_guard_dir=os.environ.get("PROMPT_GUARD_DIR") or None,
            pii=_flag("SCREEN_PII", True),
            # Off unless explicitly turned on (spans go to Langfuse, an outside service).
            tracing=_flag("TRACING", False),
            langfuse_public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
            langfuse_secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
            langfuse_host=os.environ.get("LANGFUSE_HOST", ""),
        )
