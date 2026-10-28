import json

from prpilot_data.records import read_jsonl


def test_read_jsonl_keeps_unicode_line_separators_inside_strings(tmp_path):
    rows = [{"body": "first second third\x85fourth"}, {"body": "plain"}]
    path = tmp_path / "x.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    # str.splitlines() would cut the first line at U+2028 and fail to parse it.
    assert len(path.read_text(encoding="utf-8").splitlines()) > 2
    assert read_jsonl(path) == rows


def test_read_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text('{"a": 1}\n\n{"a": 2}\n', encoding="utf-8")
    assert read_jsonl(path) == [{"a": 1}, {"a": 2}]
