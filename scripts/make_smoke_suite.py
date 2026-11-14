"""Build the CI smoke suite: 50 validation PRs with recorded model replies (Phase 2).

Takes the first 50 PRs of the seeded 500-PR sample (prompts_val_s500.jsonl) and, for three
recorded runs (untuned Qwen3-1.7B, the teacher, Gemini), copies their raw replies. Writes
eval/tests/smoke/:
  pack.jsonl      repo, number and the repo rules per PR (no PR text is committed)
  targets.jsonl   the maintainer targets
  <run>.jsonl     the recorded replies (+ .meta.json naming the source file and its hash);
                  the teacher's <think> block is dropped to keep the fixture small (both
                  validators strip it before parsing anyway)
  expected.json   what the eval harness scores for each run, and what the app validator
                  (prpilot_core) says about each reply
CI re-scores and re-validates them (eval/tests/test_smoke.py, tests/test_smoke_app.py), so a
change to either validator or to the metrics shows up as a diff against recorded behaviour.

Usage (after the replies exist under data/processed/replies/):
    uv run python scripts/make_smoke_suite.py
"""

import hashlib
import json
import subprocess
from pathlib import Path

from prpilot_core.agents.triage_format import parse_static, repo_rule_errors, strip_think
from prpilot_core.deps import RepoRules, TriageDeps

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
OUT = ROOT / "eval" / "tests" / "smoke"
N = 50
RUNS = {
    "qwen3-1.7b": "qwen3-1.7b_val_11852470",
    "teacher": "teacher_val_11852471",
    "gemini-3.8-flash": "gemini-3.8-flash_val_s500",
}


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def app_verdict(text: str, rules: dict) -> dict:
    deps = TriageDeps(
        rules=RepoRules(
            labels=frozenset(rules["labels"]),
            eligible_reviewers=frozenset(rules["eligible_reviewers"]),
            open_issues=dict.fromkeys(rules["open_issues"], ""),
        ),
        pr_author=rules["pr_author"],
    )
    try:
        out = parse_static(strip_think(text))
    except ValueError:  # pydantic ValidationError subclasses ValueError
        return {"static": False, "repo_rules": False}
    return {"static": True, "repo_rules": not repo_rule_errors(out, deps)}


def main() -> None:
    sample = read_jsonl(PROCESSED / "prompts_val_s500.jsonl")[:N]
    keys = [(r["repo"], r["number"]) for r in sample]
    targets = {(t["repo"], t["number"]): t for t in read_jsonl(PROCESSED / "targets_val.jsonl")}
    OUT.mkdir(parents=True, exist_ok=True)
    pack = [{"repo": r["repo"], "number": r["number"], "rules": r["rules"]} for r in sample]
    write_jsonl(OUT / "pack.jsonl", pack)
    write_jsonl(OUT / "targets.jsonl", [targets[k] for k in keys])
    pack_sha = sha256(OUT / "pack.jsonl")

    expected = {"source_sample": sha256(PROCESSED / "prompts_val_s500.jsonl"), "runs": {}}
    for name, source in RUNS.items():
        src = PROCESSED / "replies" / f"{source}.jsonl"
        src_meta = json.loads(src.with_name(f"{source}.meta.json").read_text(encoding="utf-8"))
        by_key = {(r["repo"], r["number"]): r for r in read_jsonl(src)}
        replies = []
        for k in keys:
            r = by_key[k]
            text = r["text"].split("</think>", 1)[-1] if name == "teacher" else r["text"]
            replies.append({"repo": k[0], "number": k[1], "text": text})
        write_jsonl(OUT / f"{name}.jsonl", replies)
        meta = {
            "name": name,
            "pack_sha256": pack_sha,
            "source": {"file": src.name, "sha256": sha256(src), "model": src_meta["model"]},
        }
        (OUT / f"{name}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", "utf-8")
        report = json.loads(
            subprocess.run(
                ["uv", "run", "--locked", "python", "-c", SCORE, str(OUT), name],
                cwd=ROOT / "eval",
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        rules = {(p["repo"], p["number"]): p["rules"] for p in pack}
        expected["runs"][name] = {
            "metrics": report,
            "app_verdicts": [
                app_verdict(r["text"], rules[(r["repo"], r["number"])]) for r in replies
            ],
        }
    (OUT / "expected.json").write_text(json.dumps(expected, indent=1) + "\n", "utf-8")
    print(f"wrote {OUT} ({N} PRs, runs: {', '.join(RUNS)})")


# Scored by the eval harness in its own environment (it never imports app code).
SCORE = """
import json, sys
from pathlib import Path
from prpilot_eval.__main__ import score
d, name = Path(sys.argv[1]), sys.argv[2]
report, _ = score(d / "pack.jsonl", d / "targets.jsonl", d / f"{name}.jsonl")
print(json.dumps(report["metrics"]))
"""

if __name__ == "__main__":
    main()
