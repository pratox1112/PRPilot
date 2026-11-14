"""PRPilot eval harness (roadmap §5.3).

Isolated on purpose: its own pinned dependencies, and it never imports app code. Inputs are
files: a prompt pack (the exact model inputs), a targets file (maintainer answers) and the
model's raw replies. Every reply is parsed once, first attempt, so framework retries or
constrained decoding cannot inflate the model metrics (hard rule 10).
"""
