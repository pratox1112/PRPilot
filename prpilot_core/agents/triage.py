"""Triage agent: Pydantic AI wiring around the shared prompt and validation.

The agent uses text output: the model's reply must be one raw JSON object, which we parse
ourselves. Pydantic AI adds nothing to the prompt in this mode (ADR 0001).
"""

from pydantic import ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.output import TextOutput

from prpilot_core.agents.triage_format import (
    SYSTEM_PROMPT,
    PRContext,
    build_user_prompt,
    parse_static,
    repo_rule_errors,
)
from prpilot_core.deps import TriageDeps
from prpilot_core.schemas import TriageOutput

__all__ = [
    "DEFAULT_OUTPUT_RETRIES",
    "SYSTEM_PROMPT",
    "PRContext",
    "build_triage_agent",
    "build_user_prompt",
    "parse_static",
    "parse_triage_output",
    "repo_rule_errors",
]

DEFAULT_OUTPUT_RETRIES = 2


def _format_error(err) -> str:
    loc = ".".join(map(str, err["loc"])) or "root"
    return f"{loc}: {err['msg']}"


def parse_triage_output(ctx: RunContext[TriageDeps], text: str) -> TriageOutput:
    try:
        out = parse_static(text)
    except ValidationError as e:
        details = "; ".join(_format_error(err) for err in e.errors())
        raise ModelRetry(f"Output is not valid against the schema: {details}") from e
    errors = repo_rule_errors(out, ctx.deps)
    if errors:
        raise ModelRetry("Output breaks repository rules: " + "; ".join(errors))
    return out


def build_triage_agent(
    model: Model, output_retries: int = DEFAULT_OUTPUT_RETRIES
) -> Agent[TriageDeps, TriageOutput]:
    return Agent(
        model,
        output_type=TextOutput(parse_triage_output),
        deps_type=TriageDeps,
        system_prompt=SYSTEM_PROMPT,
        retries={"output": output_retries},
        name="triage",
    )
