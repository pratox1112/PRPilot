"""Run metadata and result writing for everything that records a number (hard rule 1)."""

import hashlib
import json
import platform
import subprocess
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def porcelain_paths(status: str) -> list[str]:
    """Paths from `git status --porcelain` output (each line is "XY path")."""
    return [line[3:] for line in status.split("\n") if line.strip()]


def git_info() -> dict:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    dirty = porcelain_paths(status)
    return {"commit": _git("rev-parse", "HEAD"), "dirty": bool(dirty), "dirty_paths": dirty}


def require_clean_tree() -> None:
    """Refuse to start a recorded run on uncommitted changes (hard rule 1)."""
    info = git_info()
    if info["dirty"]:
        listing = "\n  ".join(info["dirty_paths"])
        raise SystemExit(f"uncommitted changes; commit them before this run:\n  {listing}")


def content_hash(obj) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def run_metadata(packages: list[str]) -> dict:
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": git_info(),
        "python": platform.python_version(),
        "packages": {name: version(name) for name in packages},
    }


def write_result(name: str, payload: dict) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSON-lines file. Splits on "\n" only: `str.splitlines()` also breaks on Unicode
    separators such as U+2028, which can appear unescaped inside JSON strings in PR text."""
    with path.open(encoding="utf-8", newline="") as f:
        return [json.loads(line) for line in f.read().split("\n") if line.strip()]
