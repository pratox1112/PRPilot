"""PII redaction in PR prose with Microsoft Presidio (roadmap §7.3).

Presidio runs on prose only (title, body): on diffs it mistakes code for PII (task 0.7 saw
`cfg.scale` redacted as a URL), so diffs go through the secret scanner instead. The entity
list and score threshold are fixed here, before any evaluation, so the Kaggle F5 score is
not tuned on the data it is reported on. URLs and GitHub @handles are not redacted: they are
public, PR bodies use them to link issues and ping reviewers, and removing them would hurt
triage more than it protects anyone.

presidio and spaCy are imported lazily: they live in the `screening` dependency group.
"""

import re
from dataclasses import dataclass

ENTITIES = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "IP_ADDRESS",
    "CREDIT_CARD",
    "IBAN_CODE",
    "US_SSN",
)
SCORE_THRESHOLD = 0.5
SPACY_MODEL = "en_core_web_lg"  # pinned to 3.8.0 in pyproject.toml


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    entity: str
    score: float


class PIIRedactor:
    def __init__(self, entities: tuple[str, ...] = ENTITIES, threshold: float = SCORE_THRESHOLD):
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        nlp = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": SPACY_MODEL}],
            }
        ).create_engine()
        self.analyzer = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
        self.entities = list(entities)
        self.threshold = threshold

    def find(self, text: str) -> list[Span]:
        return merge_spans(self.find_all(text))

    def find_all(self, text: str) -> list[Span]:
        """Every span above the threshold, mentions excluded, before overlap merging (for
        scoring several entity subsets from one analysis)."""
        if not text.strip():
            return []
        results = self.analyzer.analyze(
            text=text, language="en", entities=self.entities, score_threshold=self.threshold
        )
        spans = [Span(r.start, r.end, r.entity_type, r.score) for r in results]
        return [s for s in spans if not is_mention(text, s)]

    def redact(self, text: str) -> tuple[str, list[Span]]:
        spans = self.find(text)
        return redact_spans(text, spans), spans


MENTION = re.compile(r"(?<![\w.])@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")


def is_mention(text: str, span: Span) -> bool:
    """A PERSON span that covers a GitHub @handle (public, and a reviewer signal): kept.
    Email addresses are a separate entity and are still redacted."""
    if span.entity != "PERSON":
        return False
    start = max(span.start - 1, 0)  # spaCy may leave the "@" just outside the span
    return MENTION.search(text, start, span.end) is not None


def merge_spans(spans) -> list[Span]:
    """Drop spans overlapping a higher-scoring (then longer) one; return in text order."""
    kept: list[Span] = []
    for s in sorted(spans, key=lambda s: (-s.score, -(s.end - s.start), s.start)):
        if all(s.end <= k.start or s.start >= k.end for k in kept):
            kept.append(s)
    return sorted(kept, key=lambda s: s.start)


def redact_spans(text: str, spans: list[Span]) -> str:
    out, pos = [], 0
    for s in spans:  # in text order, non-overlapping
        out += [text[pos : s.start], f"<{s.entity}>"]
        pos = s.end
    return "".join(out) + text[pos:]
