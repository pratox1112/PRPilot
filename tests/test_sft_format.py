"""Training/inference format match (roadmap §6.1): the training example's prompt tokens must be
exactly the inference prompt, and the whole example must equal the chat template's rendering.
Needs transformers and the pinned Qwen3 tokenizer (skipped in CI; the training job runs the same
check on every example before it starts)."""

import sys
from pathlib import Path

import pytest

transformers = pytest.importorskip("transformers")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training" / "sft"))
import format as sft_format  # noqa: E402

from prpilot_core.agents.triage_format import SYSTEM_PROMPT  # noqa: E402

REPO, REVISION = "Qwen/Qwen3-1.7B", "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"


@pytest.fixture(scope="module")
def tok():
    try:
        return transformers.AutoTokenizer.from_pretrained(REPO, revision=REVISION)
    except OSError as e:  # offline and not cached
        pytest.skip(f"tokenizer unavailable: {e}")


def test_training_prompt_is_the_inference_prompt(tok):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "<pr_title>\nFix a crash\n</pr_title>"},
        {"role": "assistant", "content": '{"summary": "Fixes a crash.", "type": "bug"}'},
    ]
    ids, labels = sft_format.encode(tok, messages)
    prompt = sft_format.prompt_ids(tok, messages)
    assert ids[: len(prompt)] == prompt
    assert all(label == sft_format.IGNORE for label in labels[: len(prompt)])
    answer = tok.decode([i for i, label in zip(ids, labels, strict=True) if label != -100])
    assert answer == messages[2]["content"] + sft_format.END_OF_TURN
    assert tok.decode(prompt).endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
    assert sft_format.check(tok, messages) is None
