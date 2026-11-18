"""Postgres schema (SQLAlchemy 2): pipeline state, proposals, the approval lock, memory.

The lock's guarantees are database constraints, not just application checks:
- one human decision per (proposal, action): `uq_approval_action`;
- an approval can be used by at most one execution: `uq_execution_approval`;
- an action runs successfully at most once: the partial unique index `uq_execution_done`.
Every executor refusal is written to `refusals`, so failing closed is visible.

Pipeline steps are keyed by (repo, PR number, head SHA): a redelivered or repeated event
finds the existing run and skips the steps it already finished.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Delivery(Base):
    """Webhook deliveries seen, for dropping redeliveries (X-GitHub-Delivery)."""

    __tablename__ = "deliveries"

    delivery_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event: Mapped[str] = mapped_column(String(64))
    repo: Mapped[str | None] = mapped_column(String(200))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    received_at: Mapped[datetime] = _now()


class PRRun(Base):
    """One pipeline run per (repo, PR, head SHA); each step stores its output here."""

    __tablename__ = "pr_runs"
    __table_args__ = (UniqueConstraint("repo", "pr_number", "head_sha", name="uq_run_pr_sha"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo: Mapped[str] = mapped_column(String(200))
    pr_number: Mapped[int] = mapped_column(Integer)
    head_sha: Mapped[str] = mapped_column(String(64))
    # pending -> screened | quarantined -> triaged -> proposed; or failed
    state: Mapped[str] = mapped_column(String(20), server_default="pending")
    screen: Mapped[dict | None] = mapped_column(JSONB)  # field scores, PII/secret counts
    triage: Mapped[dict | None] = mapped_column(JSONB)  # TriageOutput + agent metadata
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Proposal(Base):
    """What the agent proposes for one run: a list of actions, frozen by a content hash."""

    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[int] = mapped_column(ForeignKey("pr_runs.id"), unique=True)
    repo: Mapped[str] = mapped_column(String(200))
    pr_number: Mapped[int] = mapped_column(Integer)
    head_sha: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict] = mapped_column(JSONB)  # {"actions": [...], "triage": {...}, ...}
    content_hash: Mapped[str] = mapped_column(String(71))  # "sha256:" + 64 hex
    created_at: Mapped[datetime] = _now()


class Approval(Base):
    """A human decision on one action of a proposal, bound to the hash the human saw."""

    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("proposal_id", "action_id", name="uq_approval_action"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"))
    action_id: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(10))  # approve | reject
    content_hash: Mapped[str] = mapped_column(String(71))
    approver: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = _now()


class Execution(Base):
    """One executor attempt at one approved action."""

    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint("approval_id", name="uq_execution_approval"),
        Index(
            "uq_execution_done",
            "proposal_id",
            "action_id",
            unique=True,
            postgresql_where=text("status = 'done'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("proposals.id"))
    action_id: Mapped[str] = mapped_column(String(32))
    approval_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approvals.id"))
    status: Mapped[str] = mapped_column(String(10))  # running | done | error
    dry_run: Mapped[bool] = mapped_column(server_default=text("false"))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


class Refusal(Base):
    """Every time the executor fails closed, and why."""

    __tablename__ = "refusals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action_id: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class ReviewerHistory(Base):
    """Memory: who reviewed changes under which path prefix (training window + live)."""

    __tablename__ = "reviewer_history"
    __table_args__ = (
        UniqueConstraint("repo", "path_prefix", "login", "source", name="uq_reviewer_hist"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo: Mapped[str] = mapped_column(String(200))
    path_prefix: Mapped[str] = mapped_column(String(500))
    login: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(10))  # train | live
    count: Mapped[int] = mapped_column(Integer, server_default="0")


class LabelStat(Base):
    """Memory: the repo's label taxonomy and how often each label was used."""

    __tablename__ = "label_stats"
    __table_args__ = (UniqueConstraint("repo", "label", name="uq_label_stat"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo: Mapped[str] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(200))
    count: Mapped[int] = mapped_column(Integer, server_default="0")
