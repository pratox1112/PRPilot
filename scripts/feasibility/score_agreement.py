"""Task 0.6, step 3 (laptop): score teacher type agreement with maintainers.

For each mode (thinking off / on): the teacher's first reply is parsed once against the
static schema (no retries, no constrained decoding — hard rule 10). An invalid reply counts
as a disagreement. Agreement = teacher `type` == maintainer primary type.
Go if the better mode reaches >= 60% (choosing the mode on train-window PRs is allowed).

Usage:
    uv run python scripts/feasibility/score_agreement.py --outputs <teacher_agreement_JOB.jsonl>
"""

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from common import REPO_ROOT, run_metadata, write_result
from pydantic import ValidationError

from prpilot_core.agents.triage_format import parse_static, strip_think
from prpilot_data.records import read_jsonl

INPUTS = REPO_ROOT / "data" / "processed" / "teacher_agreement_inputs.jsonl"
GO_THRESHOLD = 0.60


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 0.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(c - h, 0.0), 3), round(min(c + h, 1.0), 3)]


def teacher_type(text: str) -> str | None:
    try:
        return parse_static(strip_think(text)).type.value
    except ValidationError:
        return None


def score_mode(truth: dict, outputs: dict, mode: str) -> dict:
    n = agree = valid = agree_any = 0
    by_repo: dict[str, Counter] = defaultdict(Counter)
    by_type: dict[str, Counter] = defaultdict(Counter)
    confusion: dict[str, Counter] = defaultdict(Counter)
    finish = Counter()
    for key, t in truth.items():
        out = outputs[key][mode]
        pred = teacher_type(out["text"])
        n += 1
        finish[out["finish_reason"]] += 1
        ok = pred == t["maintainer_type"]
        valid += pred is not None
        agree += ok
        agree_any += pred in t["maintainer_types"]
        by_repo[t["repo"]]["n"] += 1
        by_repo[t["repo"]]["agree"] += ok
        by_type[t["maintainer_type"]]["n"] += 1
        by_type[t["maintainer_type"]]["agree"] += ok
        confusion[t["maintainer_type"]][pred or "INVALID"] += 1
    return {
        "n": n,
        "agreement": round(agree / n, 4),
        "agreement_ci95": wilson(agree, n),
        "agree_count": agree,
        "valid_json_first_attempt": valid,
        "agreement_with_any_maintainer_type": round(agree_any / n, 4),
        "finish_reasons": dict(finish),
        "by_repo": {
            r: {**c, "agreement": round(c["agree"] / c["n"], 3)} for r, c in by_repo.items()
        },
        "by_type": {
            t: {**c, "agreement": round(c["agree"] / c["n"], 3)} for t, c in by_type.items()
        },
        "confusion": {t: dict(c) for t, c in confusion.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", required=True)
    args = parser.parse_args()

    in_bytes = INPUTS.read_bytes()
    truth = {}
    for r in read_jsonl(INPUTS):
        truth[(r["repo"], r["number"])] = r
    out_path = Path(args.outputs)
    out_bytes = out_path.read_bytes()
    lines = [ln for ln in out_bytes.decode("utf-8").split("\n") if ln.strip()]
    teacher_meta = json.loads(lines[0])["meta"]
    outputs = {(r["repo"], r["number"]): r for r in map(json.loads, lines[1:])}

    inputs_hash = "sha256:" + hashlib.sha256(in_bytes).hexdigest()
    if teacher_meta["inputs_hash"] != inputs_hash:
        raise SystemExit("teacher outputs were produced from a different inputs file")
    if set(outputs) != set(truth):
        raise SystemExit("teacher outputs do not cover exactly the sampled PRs")

    modes = {m: score_mode(truth, outputs, m) for m in ("thinking_off", "thinking_on")}
    best = max(modes, key=lambda m: modes[m]["agreement"])
    payload = {
        "task": "0.6",
        "meta": run_metadata(["pydantic"]),
        "teacher": teacher_meta,
        "inputs_hash": inputs_hash,
        "outputs_file": out_path.name,
        "outputs_hash": "sha256:" + hashlib.sha256(out_bytes).hexdigest(),
        "metric": (
            "Teacher type == maintainer primary type, first attempt, static-schema parse, "
            "invalid reply = disagreement. Model metric (no retries, no constrained decoding)."
        ),
        "go_threshold": GO_THRESHOLD,
        "best_mode": best,
        "decision": "go" if modes[best]["agreement"] >= GO_THRESHOLD else "no-go",
        "modes": modes,
    }
    path = write_result("feasibility_teacher_agreement", payload)
    for m, s in modes.items():
        print(
            f"{m}: agreement {s['agree_count']}/{s['n']} = {s['agreement']:.1%} "
            f"(95% CI {s['agreement_ci95']}), valid JSON {s['valid_json_first_attempt']}/{s['n']}"
        )
        print(f"   by repo: { {r: v['agreement'] for r, v in s['by_repo'].items()} }")
    print(f"best: {best} -> {payload['decision']}\nwrote {path}")


if __name__ == "__main__":
    main()
