"""Training examples as token ids, built so the prompt is exactly what inference sends.

At inference (vLLM `llm.chat`, and the agent through vLLM's OpenAI server) the prompt is the
pack's system + user messages rendered by the model's chat template with
`add_generation_prompt=True, enable_thinking=False`. Training uses that same call for the prompt
and appends the answer plus the template's end-of-turn, with the loss on the answer only.
`check` proves the result equals the template's own rendering of the full conversation, so
training and inference cannot drift apart. The training job runs it on every example first.

    python training/sft/format.py --check <sft.jsonl> --model <repo> --revision <sha>
"""

import argparse
import json
import sys
from pathlib import Path

END_OF_TURN = "<|im_end|>\n"  # Qwen3 chat template: how an assistant turn ends
IGNORE = -100  # label value the loss skips


def _ids(rendered) -> list[int]:
    # transformers may return a BatchEncoding (dict) or a plain list, depending on version.
    return list(rendered["input_ids"] if hasattr(rendered, "keys") else rendered)


def prompt_ids(tokenizer, messages: list[dict]) -> list[int]:
    """The inference prompt: system + user, generation prompt, thinking off."""
    return _ids(
        tokenizer.apply_chat_template(
            messages[:2], add_generation_prompt=True, enable_thinking=False, tokenize=True
        )
    )


def encode(tokenizer, messages: list[dict]) -> tuple[list[int], list[int]]:
    """(input_ids, labels) for one example; prompt tokens are masked out of the loss."""
    prompt = prompt_ids(tokenizer, messages)
    answer = tokenizer(messages[2]["content"] + END_OF_TURN, add_special_tokens=False)
    answer_ids = _ids(answer)
    return prompt + answer_ids, [IGNORE] * len(prompt) + answer_ids


def check(tokenizer, messages: list[dict]) -> str | None:
    """None if encode() equals the chat template's rendering of the whole conversation."""
    full = _ids(tokenizer.apply_chat_template(messages, enable_thinking=False, tokenize=True))
    ids, _ = encode(tokenizer, messages)
    if full == ids:
        return None
    at = next((i for i, (a, b) in enumerate(zip(full, ids, strict=False)) if a != b), None)
    at = min(len(full), len(ids)) if at is None else at
    return f"differs at token {at}: template {full[at : at + 8]} vs encode {ids[at : at + 8]}"


def main() -> None:
    from transformers import AutoTokenizer

    p = argparse.ArgumentParser()
    p.add_argument("--check", type=Path, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--revision", required=True)
    args = p.parse_args()
    tok = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    n = bad = 0
    with args.check.open(encoding="utf-8") as f:
        for line in f:
            n += 1
            row = json.loads(line)
            err = check(tok, row["messages"])
            if err:
                bad += 1
                if bad <= 5:
                    print(f"{row['repo']}#{row['number']}: {err}")
    print(f"format check: {n - bad}/{n} examples match the chat template")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
