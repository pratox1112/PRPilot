"""Download the worker's pinned model files at image build time, so the running worker never
calls the Hugging Face Hub (it runs with HF_HUB_OFFLINE=1):

- PromptGuard 2 86M at its pinned revision, into OUT/prompt-guard (gated: needs HF_TOKEN,
  read from the build secret file given as the second argument, never stored in the image);
- the student's tokenizer.json at its pinned revision, into the Hugging Face cache (HF_HOME),
  for the diff token budget.

    python -I scripts/fetch_worker_models.py /opt/models /run/secrets/hf_token
"""

import sys
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

from prpilot_core.screening.prompt_guard import PROMPT_GUARD_86M
from prpilot_data.tokens import STUDENT_MODEL, STUDENT_REVISION


def main(out: str, token_file: str) -> None:
    token = Path(token_file).read_text().strip()
    snapshot_download(
        PROMPT_GUARD_86M.repo,
        revision=PROMPT_GUARD_86M.revision,
        local_dir=Path(out) / "prompt-guard",
        token=token,
    )
    hf_hub_download(STUDENT_MODEL, "tokenizer.json", revision=STUDENT_REVISION, token=token)


if __name__ == "__main__":
    main(*sys.argv[1:3])
