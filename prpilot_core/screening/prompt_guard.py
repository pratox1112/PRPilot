"""Prompt-injection screening with Meta's PromptGuard 2, loaded at a pinned revision.

PromptGuard reads at most 512 tokens. PR diffs are much longer, and truncating would let a
payload hide past the cut, so the text is split into overlapping windows and the PR is
scored by its most suspicious window.

torch and transformers are imported lazily: they live in the `screening` dependency group,
and the chunking logic must stay importable (and testable) without them.
"""

from dataclasses import dataclass

DEFAULT_THRESHOLD = 0.9  # LlamaFirewall's default block threshold for PromptGuard
MAX_TOKENS = 512


@dataclass(frozen=True)
class GuardModel:
    repo: str
    revision: str


PROMPT_GUARD_86M = GuardModel(
    "meta-llama/Llama-Prompt-Guard-2-86M", "a8ded8e697ce7c355e395a0df51f94adb4a2fd27"
)
PROMPT_GUARD_22M = GuardModel(
    "meta-llama/Llama-Prompt-Guard-2-22M", "11614a155199674a0a95e6602d6ab0417b790ed0"
)


@dataclass(frozen=True)
class GuardResult:
    score: float
    blocked: bool
    n_windows: int
    window_scores: tuple[float, ...]


def window_spans(n_tokens: int, size: int, stride: int) -> list[tuple[int, int]]:
    """Token spans of at most `size` that cover [0, n_tokens), each starting `stride` after
    the previous one. The last window always reaches the end of the text."""
    if size <= 0 or not 0 < stride <= size:
        raise ValueError("need size > 0 and 0 < stride <= size")
    if n_tokens <= size:
        return [(0, n_tokens)]
    spans = [(start, start + size) for start in range(0, n_tokens - size, stride)]
    spans.append((n_tokens - size, n_tokens))
    return spans


def _malicious_index(id2label: dict[int, str]) -> int:
    for idx, name in id2label.items():
        if any(k in name.upper() for k in ("MALICIOUS", "INJECTION", "JAILBREAK")):
            return int(idx)
    return max(int(i) for i in id2label)


class PromptGuard:
    def __init__(
        self,
        model: GuardModel = PROMPT_GUARD_86M,
        threshold: float = DEFAULT_THRESHOLD,
        stride: int = 256,
        cache_dir: str | None = None,
        token: str | None = None,
        batch_size: int = 16,
    ):
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        path = snapshot_download(
            model.repo, revision=model.revision, cache_dir=cache_dir, token=token
        )
        self._torch = torch
        self.model_info = model
        self.tokenizer = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForSequenceClassification.from_pretrained(path).eval()
        self.threshold = threshold
        self.batch_size = batch_size
        self.content_size = MAX_TOKENS - self.tokenizer.num_special_tokens_to_add()
        self.stride = min(stride, self.content_size)
        self.malicious_index = _malicious_index(self.model.config.id2label)

    def score(self, text: str) -> GuardResult:
        ids = self.tokenizer(text, add_special_tokens=False)["input_ids"]
        spans = window_spans(len(ids), self.content_size, self.stride)
        windows = [self.tokenizer.build_inputs_with_special_tokens(ids[s:e]) for s, e in spans]
        scores: list[float] = []
        for i in range(0, len(windows), self.batch_size):
            batch = self.tokenizer.pad(
                {"input_ids": windows[i : i + self.batch_size]}, return_tensors="pt"
            )
            with self._torch.no_grad():
                logits = self.model(**batch).logits
            probs = self._torch.softmax(logits, dim=-1)[:, self.malicious_index]
            scores += probs.tolist()
        best = max(scores)
        return GuardResult(best, best >= self.threshold, len(windows), tuple(scores))

    def score_first_window_only(self, text: str) -> float:
        """What a scanner that truncates at 512 tokens would see (for comparison only)."""
        batch = self.tokenizer(text, truncation=True, max_length=MAX_TOKENS, return_tensors="pt")
        with self._torch.no_grad():
            logits = self.model(**batch).logits
        return self._torch.softmax(logits, dim=-1)[0, self.malicious_index].item()
