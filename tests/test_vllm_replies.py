"""Checkpoint discovery for scoring a training run's LoRA adapters (vllm_replies --lora-dir)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training" / "baselines"))
import vllm_replies  # noqa: E402


def test_checkpoints_in_step_order_with_final_last(tmp_path):
    for name in ("checkpoint-1000", "checkpoint-200", "final", "checkpoint-400"):
        (tmp_path / name).mkdir()
    (tmp_path / "mlflow_run_id.txt").write_text("x")
    names = [p.name for p in vllm_replies.checkpoints(tmp_path)]
    assert names == ["checkpoint-200", "checkpoint-400", "checkpoint-1000", "final"]


def test_adapter_info_hashes_the_weights(tmp_path):
    (tmp_path / "adapter_model.safetensors").write_bytes(b"weights")
    info = vllm_replies.adapter_info(tmp_path)
    assert info["checkpoint"] == tmp_path.name
    assert info["sha256"].startswith("sha256:") and len(info["sha256"]) == 71
