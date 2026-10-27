import pytest

from prpilot_core.screening.prompt_guard import _malicious_index, special_wrap, window_spans


def test_special_wrap_cls_sep():
    assert special_wrap([1, 50, 51, 2], [50, 51]) == ([1], [2])


def test_special_wrap_none_and_error():
    assert special_wrap([50, 51], [50, 51]) == ([], [])
    with pytest.raises(ValueError):
        special_wrap([1, 9, 2], [50])


@pytest.mark.parametrize("n", [0, 1, 509, 510])
def test_short_text_is_one_window(n):
    assert window_spans(n, 510, 256) == [(0, n)]


@pytest.mark.parametrize("n", [511, 766, 1000, 5000])
def test_windows_cover_every_token_and_respect_size(n):
    spans = window_spans(n, 510, 256)
    covered = set()
    for s, e in spans:
        assert 0 <= s < e <= n and e - s <= 510
        covered.update(range(s, e))
    assert covered == set(range(n))
    assert spans[-1][1] == n


def test_consecutive_windows_overlap():
    spans = window_spans(2000, 510, 256)
    for (_, e1), (s2, _) in zip(spans, spans[1:], strict=False):
        assert s2 < e1


def test_invalid_arguments():
    with pytest.raises(ValueError):
        window_spans(10, 0, 1)
    with pytest.raises(ValueError):
        window_spans(10, 5, 6)


def test_malicious_index_by_name_or_last():
    assert _malicious_index({0: "BENIGN", 1: "MALICIOUS"}) == 1
    assert _malicious_index({0: "LABEL_0", 1: "LABEL_1"}) == 1
