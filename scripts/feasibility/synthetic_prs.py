"""Synthetic PRs shared by the feasibility checks (0.8 student, 0.6 teacher smoke test).

Written by hand for these checks; no real data. Kept in one place so every model sees the
same inputs.
"""

from prpilot_core.agents.triage_format import PRContext
from prpilot_core.deps import RepoRules

RULES = RepoRules(
    labels=frozenset({"kind/bug", "kind/feature", "kind/docs", "kind/cleanup", "area/deps"}),
    eligible_reviewers=frozenset({"alice-dev", "bob-maint", "chen-w"}),
    open_issues={101: "Crash when config file is empty", 102: "Document retry settings"},
)
SAMPLES = [
    PRContext(
        title="Fix crash when config file is empty",
        body="Loading an empty YAML file returned None and crashed later. Fixes #101.",
        author="dana-c",
        base_branch="main",
        changed_files=("src/app/config.py", "tests/test_config.py"),
        diff=(
            "--- a/src/app/config.py\n+++ b/src/app/config.py\n"
            "@@ -10,6 +10,8 @@ def load(path):\n     data = yaml.safe_load(f)\n"
            "+    if data is None:\n+        return {}\n     return data\n"
        ),
    ),
    PRContext(
        title="docs: explain retry settings",
        body="Adds a section on `max_retries` and backoff to the configuration guide.",
        author="erin-k",
        base_branch="main",
        changed_files=("docs/configuration.md",),
        diff=(
            "--- a/docs/configuration.md\n+++ b/docs/configuration.md\n"
            "@@ -40,3 +40,12 @@\n+## Retries\n+\n+`max_retries` sets how many times...\n"
        ),
    ),
    PRContext(
        title="Bump requests from 2.31.0 to 2.32.3",
        body="Bumps requests to pick up the security fix for certificate verification.",
        author="frank-o",
        base_branch="main",
        changed_files=("requirements.txt",),
        diff=(
            "--- a/requirements.txt\n+++ b/requirements.txt\n"
            "@@ -3 +3 @@\n-requests==2.31.0\n+requests==2.32.3\n"
        ),
    ),
]
