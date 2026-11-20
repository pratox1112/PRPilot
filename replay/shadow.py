"""Shadow replay: the deployed system's runs on a replay bundle, written as replies the eval
harness scores against maintainer truth, next to the offline runs of the same model file.

    uv run python -m replay.shadow prompts --bundle data/processed/replay/shadow_test500.jsonl \
        --pack data/processed/prompts_test.jsonl
    uv run python -m replay.shadow replies --label shadow --api API_URL \
        --pack data/processed/prompts_test.jsonl

`prompts` (free, local, no model) rebuilds every bundled PR's prompt the way the worker does
(the replay client and the same context builder, before screening) and checks that it is
byte-identical to the pack's, so the deployed and the offline runs score the same inputs.
Screening can still redact secrets or personal data in the deployed prompt; `replies` counts
the runs where it did.

`replies` reads the runs and proposals of the PRs a send (replay.send) sent, from the api
with ADMIN_TOKEN, and writes two reply files, each with the `.meta.json` the harness reads:
- <label>-model.jsonl: the triage output the agent returned (after validator retries, and
  after the fallback filter when that rescued an output): comparable with the offline system
  runs (the agent with retries) of the same GGUF file;
- <label>-proposal.jsonl: the stored proposal (the same output with the reviewers ordered by
  the reviewer rule): what a maintainer would have been shown.
A PR with no output (quarantined, failed, or never final) gets an empty reply, which the
harness scores as invalid. Starts only on a clean, committed tree (hard rule 1).
"""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path

import httpx

from prpilot_core.agents.triage_format import SYSTEM_PROMPT, build_user_prompt
from prpilot_core.context import build_context
from prpilot_core.github.replay import ReplayGitHubClient
from prpilot_data.records import read_jsonl, require_clean_tree, run_metadata
from replay.measure import ts
from replay.send import SENDS_DIR, secret_values

MAX_LIST = 20_000  # services.api.app.MAX_LIST


async def prompt_mismatches(bundle_path: Path, pack: dict) -> tuple[int, list[str]]:
    """How many bundled PRs were checked, and those whose rebuilt prompt is not the pack's."""
    gh = ReplayGitHubClient.from_bundle(bundle_path)
    bad = []
    keys = [(r["pr"]["repo"], r["pr"]["number"]) for r in read_jsonl(bundle_path)]
    for repo, number in keys:
        ctx = await build_context(gh, None, repo, number)  # a snapshot reads no memory
        row = pack.get((repo, number))
        if row is None or row["messages"] != [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(ctx.pr, ctx.rules)},
        ]:
            bad.append(f"{repo}#{number}")
    return len(keys), bad


def replies(sends: list[dict], runs: list[dict], proposals: list[dict]) -> dict[str, list]:
    """One reply per sent PR for each view, from its run (and proposal) of the sent head."""
    run_of = {(r["repo"], r["pr_number"], r["head_sha"]): r for r in runs}
    proposal_of: dict[tuple, dict] = {}
    for p in proposals:  # newest first: the first one seen per PR is kept
        proposal_of.setdefault((p["repo"], p["pr_number"], p["head_sha"]), p)
    out: dict[str, list] = {"model": [], "proposal": []}
    for s in sends:
        key = (s["repo"], s["number"], s["head_sha"])
        run, prop = run_of.get(key), proposal_of.get(key)
        triage = (run or {}).get("triage") or {}
        common = {"repo": s["repo"], "number": s["number"],
                  "finish_reason": run["state"] if run else "no run",
                  "requests": triage.get("requests"),
                  "partly_filtered": triage.get("partly_filtered"),
                  "latency_s": (ts(s["seen_final_at"]) - ts(s["sent_at"])).total_seconds()
                  if s.get("seen_final_at") else None}  # fmt: skip
        model_out = triage.get("output")
        out["model"].append({**common, "text": json.dumps(model_out) if model_out else ""})
        out["proposal"].append({**common, "text": json.dumps(prop["triage"]) if prop else ""})
    return out


def run_counts(sends: list[dict], runs: list[dict]) -> dict:
    keys = {(s["repo"], s["number"], s["head_sha"]) for s in sends}
    ours = [r for r in runs if (r["repo"], r["pr_number"], r["head_sha"]) in keys]
    screen = [r.get("screen") or {} for r in ours]
    return {
        "prs_sent": len(sends), "runs": len(ours),
        "states": dict(Counter(r["state"] for r in ours)),
        "partly_filtered": sum(bool((r.get("triage") or {}).get("partly_filtered")) for r in ours),
        "pii_redacted": sum(bool(s.get("pii_redacted")) for s in screen),
        "secrets_redacted": sum(bool(s.get("secrets_redacted")) for s in screen),
    }  # fmt: skip


def write_replies(path: Path, rows: list[dict], meta: dict) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8", newline="")  # fmt: skip
    meta_path = path.with_name(path.name.removesuffix(".jsonl") + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def cmd_prompts(bundle_path: Path, pack_path: Path) -> None:
    pack = {(p["repo"], p["number"]): p for p in read_jsonl(pack_path)}
    n, bad = asyncio.run(prompt_mismatches(bundle_path, pack))
    print(f"{n - len(bad)}/{n} bundled prompts identical to {pack_path.name}")
    if bad:
        raise SystemExit(f"{len(bad)} differ: {', '.join(bad[:20])}")


def cmd_replies(label: str, api: str, pack_path: Path) -> None:
    sends = read_jsonl(SENDS_DIR / f"{label}.sends.jsonl")
    _, token = secret_values()
    auth = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=300, headers=auth) as http:
        r = http.get(f"{api}/runs", params={"limit": MAX_LIST, "output": "true"})
        r.raise_for_status()
        runs = r.json()
        r = http.get(f"{api}/proposals", params={"limit": MAX_LIST})
        r.raise_for_status()
        proposals = r.json()
    counts = run_counts(sends, runs)
    meta = {
        "pack_file": pack_path.name,
        "pack_sha256": "sha256:" + hashlib.sha256(pack_path.read_bytes()).hexdigest(),
        "source": f"deployed system, replay send {label!r} via {api}",
        "runs": counts,
        **run_metadata(["httpx"]),
    }
    for view, rows in replies(sends, runs, proposals).items():
        path = SENDS_DIR / f"{label}-{view}.jsonl"
        write_replies(path, rows, {"name": f"{label}-{view}", "view": view, **meta})
        print(f"{view}: {sum(bool(x['text']) for x in rows)}/{len(rows)} with output -> {path}")
    print(json.dumps(counts))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("prompts")
    pr.add_argument("--bundle", type=Path, required=True)
    pr.add_argument("--pack", type=Path, required=True)
    rp = sub.add_parser("replies")
    rp.add_argument("--label", required=True)
    rp.add_argument("--api", required=True)
    rp.add_argument("--pack", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "prompts":
        cmd_prompts(a.bundle, a.pack)
    else:
        require_clean_tree()
        cmd_replies(a.label, a.api.rstrip("/"), a.pack)


if __name__ == "__main__":
    main()
