from prpilot_data import pii_eval as pe

TOKENS = ["Hi", ",", "I", "am", "Ana", "Lima", ".", "\n\n", "Mail", "ana@x.io"]
WS = [False, True, True, True, True, False, False, False, True, False]


def test_offsets_rebuild_the_text():
    text = "".join(t + (" " if w else "") for t, w in zip(TOKENS, WS, strict=True))
    for tok, (s, e) in zip(TOKENS, pe.token_offsets(TOKENS, WS), strict=True):
        assert text[s:e] == tok


def test_spans_become_bio_and_skip_whitespace_tokens():
    offs = pe.token_offsets(TOKENS, WS)
    text = "".join(t + (" " if w else "") for t, w in zip(TOKENS, WS, strict=True))
    name = (text.index("Ana"), text.index("Lima") + 4, "NAME_STUDENT")
    mail = (text.index("ana@"), len(text), "EMAIL")
    labels = pe.spans_to_bio(offs, TOKENS, [name, mail])
    assert labels[4:6] == ["B-NAME_STUDENT", "I-NAME_STUDENT"]
    assert labels[9] == "B-EMAIL" and labels[7] == "O"
    # a span covering the blank line still leaves the whitespace token O
    labels = pe.spans_to_bio(offs, TOKENS, [(text.index("."), text.index("Mail") + 4, "X")])
    assert labels[7] == "O" and labels[6] == "B-X" and labels[8] == "I-X"


def test_f5_weights_recall():
    gold = {(1, 4, "B-NAME_STUDENT"), (1, 5, "I-NAME_STUDENT")}
    high_recall = pe.f_beta(gold, gold | {(1, i, "B-NAME_STUDENT") for i in range(10, 18)})
    high_precision = pe.f_beta(gold, {(1, 4, "B-NAME_STUDENT")})
    assert high_recall["recall"] == 1.0 and high_recall["precision"] == 0.2
    assert high_recall["f5"] > high_precision["f5"]
    assert pe.f_beta(set(), set())["f5"] == 0.0


def test_triples_and_per_label_recall():
    gold = pe.triples("d", ["O", "B-EMAIL", "B-NAME_STUDENT", "I-NAME_STUDENT"])
    pred = pe.triples("d", ["O", "B-EMAIL", "B-NAME_STUDENT", "O"])
    assert pe.per_label_recall(gold, pred) == {
        "EMAIL": {"recall": 1.0, "gold": 1},
        "NAME_STUDENT": {"recall": 0.5, "gold": 2},
    }
    assert pe.triples("d", ["I-EMAIL"], strip_bio=True) == {("d", 0, "EMAIL")}
