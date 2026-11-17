"""The triage comment the bot proposes to post (the `draft_comment` step).

Deterministic, from the triage output: no second model call, so the comment says exactly
what the human approves in the proposal. Text the model wrote (summary, rationale) is the
PR author's content passed through screening and the model; it is quoted, never executed.
"""

from prpilot_core.schemas import TriageOutput

FOOTER = (
    "<sub>Suggested by PRPilot and posted after a maintainer approved it. "
    "Labels and reviewers are proposed separately.</sub>"
)


def draft_comment(out: TriageOutput) -> str:
    lines = [
        "**Triage summary**",
        "",
        out.summary,
        "",
        f"- Type: `{out.type.value}`",
        f"- Risk: `{out.risk.value}`",
    ]
    if out.related_issues:
        lines.append("- Related issues: " + ", ".join(f"#{n}" for n in out.related_issues))
    lines += ["", f"_Why:_ {out.rationale}", "", FOOTER]
    return "\n".join(lines)
