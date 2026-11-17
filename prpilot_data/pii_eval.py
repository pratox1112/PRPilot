"""Presidio on the Kaggle "PII Data Detection" essays, scored with F5 (roadmap §7.3).

The data is student essays, already tokenised (spaCy), with BIO labels per token:
NAME_STUDENT, EMAIL, USERNAME, ID_NUM, PHONE_NUM, URL_PERSONAL, STREET_ADDRESS. Presidio
finds character spans; a token is tagged when it overlaps a span (B- on the first token of a
span, I- after; whitespace tokens stay O), and Presidio's entity types are mapped onto the
Kaggle ones. The score is the competition's: micro F-beta with beta = 5 (recall weighted 25x
over precision) on (document, token, label) triples, labels other than O, exact BIO match.
An entity-type score that ignores B-/I- is reported next to it.

This is essays, not code: the number says how well the redactor finds PII in prose, which is
what it is used on in PR titles and bodies.
"""

from collections import Counter

# Presidio entity -> Kaggle label (types with no Kaggle counterpart are dropped)
KAGGLE = {
    "PERSON": "NAME_STUDENT",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE_NUM",
    "URL": "URL_PERSONAL",
    "LOCATION": "STREET_ADDRESS",
    "US_SSN": "ID_NUM",
    "IBAN_CODE": "ID_NUM",
    "CREDIT_CARD": "ID_NUM",
    "US_DRIVER_LICENSE": "ID_NUM",
    "US_PASSPORT": "ID_NUM",
    "US_BANK_NUMBER": "ID_NUM",
}
# Reference configuration: the app's entities plus URL and LOCATION (the app keeps URLs on
# purpose and does not look for addresses; this shows what that costs on essays).
REFERENCE_EXTRA = ("URL", "LOCATION")


def token_offsets(tokens: list[str], trailing_ws: list[bool]) -> list[tuple[int, int]]:
    """Character span of each token in the text rebuilt from tokens + trailing whitespace."""
    spans, pos = [], 0
    for tok, ws in zip(tokens, trailing_ws, strict=True):
        spans.append((pos, pos + len(tok)))
        pos += len(tok) + (1 if ws else 0)
    return spans


def spans_to_bio(offsets: list[tuple[int, int]], tokens: list[str], spans) -> list[str]:
    """spans: (start, end, kaggle_label), non-overlapping."""
    labels = ["O"] * len(tokens)
    for start, end, label in spans:
        inside = [
            i for i, (s, e) in enumerate(offsets) if s < end and e > start and tokens[i].strip()
        ]
        for j, i in enumerate(inside):
            labels[i] = ("B-" if j == 0 else "I-") + label
    return labels


def f_beta(gold: set, pred: set, beta: float = 5.0) -> dict:
    tp = len(gold & pred)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(gold) if gold else 0.0
    b2 = beta * beta
    f = (1 + b2) * p * r / (b2 * p + r) if p + r else 0.0
    return {"f5": round(f, 4), "precision": round(p, 4), "recall": round(r, 4), "tp": tp,
            "gold": len(gold), "predicted": len(pred)}  # fmt: skip


def triples(doc_id, labels: list[str], strip_bio: bool = False) -> set:
    out = set()
    for i, lab in enumerate(labels):
        if lab != "O":
            out.add((doc_id, i, lab[2:] if strip_bio else lab))
    return out


def per_label_recall(gold: set, pred: set) -> dict:
    by = Counter(t[2][2:] for t in gold)
    hit = Counter(t[2][2:] for t in gold & pred)
    return {k: {"recall": round(hit[k] / n, 4), "gold": n} for k, n in sorted(by.items())}
