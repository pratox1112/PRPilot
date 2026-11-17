"""The triage pipeline: plain async Python, every step's state in Postgres (roadmap §8.1).

    context → screen → [quarantined: stop] → triage (student, Pydantic AI) → [fallback]
    → rank reviewers (memory) → related issues → draft comment → build proposal → END

One run per (repo, PR number, head SHA). The run row is locked while it is processed, so two
deliveries of the same event cannot both triage it, and a redelivered event finds the run
finished and returns it without calling the model again (a failed run is tried again). A new
head SHA (a new push) is a new run. The run ends at the stored Proposal: approval can take
hours, and the executor is triggered by the approval itself, not by this pipeline.
"""

from dataclasses import dataclass, field

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from prpilot_core import memory
from prpilot_core.agents.fallback import salvage
from prpilot_core.agents.triage import DEFAULT_OUTPUT_RETRIES, build_triage_agent
from prpilot_core.agents.triage_format import build_user_prompt
from prpilot_core.comment import draft_comment
from prpilot_core.context import build_context
from prpilot_core.db.models import Proposal, PRRun
from prpilot_core.deps import TriageDeps
from prpilot_core.github.client import GitHubClient
from prpilot_core.proposals import build_content, content_hash
from prpilot_core.schemas import TriageOutput
from prpilot_core.screening.prompt_guard import DEFAULT_THRESHOLD
from prpilot_core.screening.screen import Guard, Redactor, screen
from prpilot_data.candidates import MAX_FILES

DONE = ("quarantined", "proposed")  # a failed run is retried when the event comes again


@dataclass
class PipelineConfig:
    threshold: float = DEFAULT_THRESHOLD
    output_retries: int = DEFAULT_OUTPUT_RETRIES
    reviewer_rule: str = memory.DEFAULT_REVIEWER_RULE
    scan_secrets: bool = True
    model_settings: dict = field(
        default_factory=lambda: {
            "temperature": 0.0,
            "seed": 0,
            "max_tokens": 1024,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
    )


@dataclass
class Pipeline:
    sessions: object  # async_sessionmaker
    github: GitHubClient
    guard: Guard
    model: Model
    pii: Redactor | None = None
    config: PipelineConfig = field(default_factory=PipelineConfig)
    count_tokens: object = None  # injected in tests; the student's tokenizer otherwise

    async def run(self, repo: str, number: int) -> PRRun:
        async with self.sessions() as session:
            ctx = await build_context(self.github, session, repo, number, self.count_tokens)
            await session.execute(
                insert(PRRun)
                .values(repo=repo, pr_number=number, head_sha=ctx.info.head_sha)
                .on_conflict_do_nothing(constraint="uq_run_pr_sha")
            )
            await session.commit()
            run = await session.scalar(
                select(PRRun)
                .where(PRRun.repo == repo, PRRun.pr_number == number,
                       PRRun.head_sha == ctx.info.head_sha)
                .with_for_update()
            )  # fmt: skip
            if run.state in DONE:  # redelivery or a concurrent duplicate: nothing to redo
                await session.commit()
                return run
            try:
                await self._process(session, run, ctx)
                await session.commit()
            except Exception as e:
                await session.rollback()  # drop the half-done step, then record the failure
                run = await session.get(PRRun, run.id)
                run.state, run.error = "failed", f"{type(e).__name__}: {e}"[:2000]
                await session.commit()
            return run

    async def _process(self, session, run: PRRun, ctx) -> None:
        cfg = self.config
        res = screen(ctx.pr, self.guard, self.pii, cfg.scan_secrets, cfg.threshold)
        run.screen = {
            "quarantined": res.quarantined,
            "guard_score": round(res.guard_score, 6),
            "field_scores": {k: round(v, 6) for k, v in res.field_scores.items()},
            "pii_redacted": res.pii,
            "secrets_redacted": [f.type for f in res.secrets],
            "threshold": cfg.threshold,
        }
        if res.quarantined:
            run.state = "quarantined"
            return
        run.state = "screened"
        deps = TriageDeps(rules=ctx.rules, pr_author=ctx.info.author, run_id=str(run.id))
        out, meta = await self._triage(build_user_prompt(res.pr, ctx.rules), deps)
        run.triage = {"output": out.model_dump(mode="json") if out else None, **meta}
        if out is None:
            run.state, run.error = "failed", meta["error"]
            return
        run.state = "triaged"
        reviewers = memory.combine_reviewers(
            list(out.reviewers), list(ctx.rules.reviewer_order), cfg.reviewer_rule
        )
        final = out.model_copy(update={"reviewers": reviewers})
        # file paths only (no PR text): live outcomes update reviewer memory under them
        notes = {**ctx.notes, "reviewer_rule": cfg.reviewer_rule, **meta,
                 "changed_files": list(ctx.pr.changed_files)[:MAX_FILES]}  # fmt: skip
        content = build_content(run.repo, run.pr_number, run.head_sha, final,
                                draft_comment(final), notes)  # fmt: skip
        session.add(Proposal(run_id=run.id, repo=run.repo, pr_number=run.pr_number,
                             head_sha=run.head_sha, content=content,
                             content_hash=content_hash(content)))  # fmt: skip
        run.state = "proposed"

    async def _triage(self, prompt: str, deps: TriageDeps) -> tuple[TriageOutput | None, dict]:
        agent = build_triage_agent(self.model, output_retries=self.config.output_retries)
        error = ""
        with capture_run_messages() as messages:
            try:
                result = await agent.run(
                    prompt, deps=deps, model_settings=ModelSettings(**self.config.model_settings)
                )
                out = result.output
            except Exception as e:  # retries exhausted, server error, context overflow
                out, error = None, f"{type(e).__name__}: {e}"[:500]
        texts = [
            "".join(p.content for p in m.parts if isinstance(p, TextPart))
            for m in messages
            if isinstance(m, ModelResponse)
        ]
        meta = {"requests": len(texts), "valid_after_retries": out is not None,
                "partly_filtered": False, "removed": {}, "error": error}  # fmt: skip
        if out is None and texts:
            salvaged = salvage(texts[-1], deps)
            if salvaged is not None:
                out, removed = salvaged
                meta.update(partly_filtered=True, removed=removed)
        return out, meta
