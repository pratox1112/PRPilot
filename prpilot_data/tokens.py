"""Token counts with the student's own tokenizer, pinned, for input budgets (Phase 1 decision 7).

Only `tokenizer.json` is downloaded (a few MB, into the Hugging Face cache); no model weights,
no torch.
"""

from collections.abc import Callable

STUDENT_MODEL = "Qwen/Qwen3-1.7B"
STUDENT_REVISION = "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"


def load_counter() -> Callable[[str], int]:
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    path = hf_hub_download(STUDENT_MODEL, "tokenizer.json", revision=STUDENT_REVISION)
    tokenizer = Tokenizer.from_file(path)

    def count(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    return count
