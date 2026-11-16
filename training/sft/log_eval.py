"""Attach validation results to a training run's MLflow run (roadmap §6.4: the run records
metrics, not only losses).

Reads eval-harness reports (results/*.json, copied to the cluster) and logs each metric's
value as `<prefix>_<metric>`, plus tags naming the report, its hash and the commit it was
scored at. Run in the training env with MLFLOW_TRACKING_URI pointing at the run's store:

    python training/sft/log_eval.py --run-dir <checkpoints/fill> --prefix val_raw \
        --report <student-fill_val_checkpoint-1016.json> --select checkpoint-1016
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")  # same file store as train.py

import mlflow  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", type=Path, required=True, help="training output dir")
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--prefix", required=True, help="e.g. val_raw or val_system")
    p.add_argument("--select", help="tag this checkpoint as the one chosen on validation")
    args = p.parse_args()
    run_id = (args.run_dir / "mlflow_run_id.txt").read_text().strip()
    raw = args.report.read_bytes()
    report = json.loads(raw)
    metrics = {
        f"{args.prefix}_{k}": v["value"]
        for k, v in report["metrics"].items()
        if isinstance(v, dict) and isinstance(v.get("value"), (int, float))
    }
    tags = {
        f"{args.prefix}_report": report["name"],
        f"{args.prefix}_report_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        f"{args.prefix}_scored_at_commit": report["git"]["commit"],
    }
    if args.select:
        tags["selected_checkpoint"] = args.select
    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics(metrics)
        mlflow.set_tags(tags)
    print(f"logged {len(metrics)} metrics and {len(tags)} tags to run {run_id}")


if __name__ == "__main__":
    main()
