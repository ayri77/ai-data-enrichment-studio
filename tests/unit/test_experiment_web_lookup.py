"""The web lookup probe: request, reply handling, comparison and outputs; no live calls."""

import json
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest
from openpyxl import Workbook
from scripts.experiment_web_lookup import (
    Prices,
    Settings,
    build_request,
    claimed_not_reported,
    compare,
    estimate_cost,
    parse_reply,
    run,
    run_row,
    summarize,
)

KEY = "sk-test-secret-0123456789"


def settings(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "question": "Find the height.",
        "answer": "number",
        "unit": "m",
        "model": "gpt-6-luna",
        "reasoning_effort": "low",
        "search_context_size": "low",
        "structured": "json_schema",
        "max_tool_calls": 3,
    }
    return Settings(**{**base, **changes})


def reply(**fields: Any) -> str:
    data = {
        "value": 8849,
        "unit": "m",
        "sources": ["https://example.org/everest"],
        "quote": "8,849 m",
        "explanation": "Official survey.",
        "confident": True,
    }
    return json.dumps({**data, **fields})


def response(
    text: str,
    *,
    searches: tuple[tuple[str, ...], ...] = (("https://example.org/everest",),),
    cited: tuple[str, ...] = ("https://example.org/everest?utm_source=openai",),
    status: str = "completed",
) -> Any:
    output: list[Any] = [
        NS(
            type="web_search_call",
            action=NS(type="search", sources=[NS(type="url", url=u) for u in urls]),
        )
        for urls in searches
    ]
    output.append(
        NS(
            type="message",
            content=[NS(annotations=[NS(type="url_citation", url=u) for u in cited])],
        )
    )
    return NS(
        status=status,
        output=output,
        output_text=text,
        usage=NS(input_tokens=1000, output_tokens=200),
    )


class FakeClient:
    def __init__(self, replies: list[Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._replies = replies
        self.responses = NS(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self._replies[len(self.calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item


def probe_workbook(path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["Mountain", "Country", "note", "expected"])
    sheet.append(["Everest", "Nepal", None, 8849])
    sheet.append(["Nowhere Peak", "Chile", "invented", None])
    workbook.save(path)
    return path


# --- request -----------------------------------------------------------------------


def test_request_for_a_number_with_a_json_schema() -> None:
    request = build_request(settings(), {"Mountain": "K2"})
    assert request["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    assert request["include"] == ["web_search_call.action.sources"]
    assert request["store"] is False
    assert request["max_tool_calls"] == 3
    assert request["reasoning"] == {"effort": "low"}
    assert "a number in m" in request["input"]
    assert "- Mountain: K2" in request["input"]
    fmt = request["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    schema = fmt["schema"]
    assert schema["properties"]["value"]["type"] == ["number", "null"]
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert "never guess" in request["instructions"].lower()


def test_request_for_text_has_no_unit_field() -> None:
    request = build_request(settings(answer="text", unit=None), {"City": "Oslo"})
    schema = request["text"]["format"]["schema"]
    assert schema["properties"]["value"]["type"] == ["string", "null"]
    assert "unit" not in schema["properties"]
    assert "Answer as: text" in request["input"]


def test_json_object_mode_asks_for_the_shape_in_words() -> None:
    request = build_request(settings(structured="json_object"), {"A": "b"})
    assert request["text"] == {"format": {"type": "json_object"}}
    assert "JSON Schema" in request["instructions"]


def test_none_mode_sends_no_text_format_and_zero_cap_omits_the_parameter() -> None:
    request = build_request(settings(structured="none", max_tool_calls=0), {"A": "b"})
    assert "text" not in request
    assert "max_tool_calls" not in request
    assert "JSON Schema" in request["instructions"]


# --- parsing -----------------------------------------------------------------------


def test_well_formed_reply_parses() -> None:
    data, problems = parse_reply(reply(), settings())
    assert problems == [] and data is not None
    assert data["value"] == 8849


def test_a_null_answer_is_a_valid_shape() -> None:
    data, problems = parse_reply(
        reply(value=None, unit=None, sources=[], quote=None, confident=False), settings()
    )
    assert problems == [] and data is not None and data["value"] is None


@pytest.mark.parametrize(
    ("text", "expected_problem"),
    [
        ("Everest is 8849 m", "not valid JSON"),
        ("[1]", "not a JSON object"),
        (json.dumps({"value": 1}), "missing key"),
        (reply(value="high"), "value is not a number"),
        (reply(sources="https://x"), "sources is not a list"),
        (reply(unit="ft"), "unit is 'ft'"),
        (reply(extra=1), "unexpected key"),
    ],
)
def test_malformed_replies_say_what_failed(text: str, expected_problem: str) -> None:
    data, problems = parse_reply(text, settings())
    assert data is None
    assert any(expected_problem in p for p in problems)


# --- comparison --------------------------------------------------------------------


def test_numbers_are_compared_within_the_tolerance() -> None:
    assert compare(8849, 8848.86, "number", 10) == "match"
    assert compare(8000, 9000, "number", 10) == "mismatch"
    assert compare(8000, 8800, "number", 10) == "match"
    assert compare(8000, 8801, "number", 10) == "mismatch"
    assert compare("8849,0", 8849, "number", 1) == "match"


def test_text_ignores_case_and_whitespace_but_not_a_substring() -> None:
    assert compare("Oslo", "  oslo ", "text", 10) == "match"
    assert compare("New  York", "new york", "text", 10) == "match"
    assert compare("Oslo", "Oslo, Norway", "text", 10) == "mismatch"
    assert compare("Oslo, Norway", "Oslo", "text", 10) == "mismatch"


def test_rows_without_expected_value_are_right_only_without_an_answer() -> None:
    assert compare(None, None, "number", 10) == "correct_no_answer"
    assert compare("  ", None, "text", 10) == "correct_no_answer"
    assert compare(None, 5, "number", 10) == "false_answer"
    assert compare(8849, None, "number", 10) == "no_answer"
    assert compare("n/a", 5, "number", 10) == "expected_unreadable"


# --- sources and cost --------------------------------------------------------------


def test_claimed_sources_missing_from_the_reported_ones_are_listed() -> None:
    reported = ["https://www.example.org/a/?utm_source=openai#top", "https://example.org/b"]
    claimed = ["https://example.org/a", "https://example.org/c", "https://example.org/b"]
    assert claimed_not_reported(claimed, reported) == ["https://example.org/c"]


def test_cost_arithmetic() -> None:
    prices = Prices(per_search=0.01, input_per_mtok=0.10, output_per_mtok=0.50)
    assert estimate_cost(4, 2_000_000, 1_000_000, prices) == pytest.approx(0.04 + 0.2 + 0.5)


# --- rows and totals ---------------------------------------------------------------


def test_row_records_citations_searches_tokens_and_comparison() -> None:
    client = FakeClient(
        [response(reply(sources=["https://example.org/everest", "https://fake.io"]))]
    )
    record = run_row(client, settings(), 2, {"Mountain": "Everest"}, 8849, True, 10, KEY)
    assert record["status"] == "answered"
    assert record["search_calls"] == 1
    assert (record["input_tokens"], record["output_tokens"]) == (1000, 200)
    assert record["annotation_urls"] == ["https://example.org/everest?utm_source=openai"]
    assert record["claimed_not_reported"] == ["https://fake.io"]
    assert record["no_api_citation"] is False
    assert record["comparison"] == "match"


def test_a_failure_is_recorded_and_does_not_stop_the_other_rows() -> None:
    boom = RuntimeError(f"Incorrect API key provided: {KEY}")
    client = FakeClient([boom, response("not json", searches=(), cited=())])
    first = run_row(client, settings(), 2, {}, None, False, 10, KEY)
    second = run_row(client, settings(), 3, {}, None, False, 10, KEY)
    assert first["status"] == "error" and KEY not in json.dumps(first)
    assert second["status"] == "shape_failure" and second["no_api_citation"] is True


def test_summary_counts_and_precision() -> None:
    ok = response(reply())
    wrong = response(reply(value=100))
    null = response(reply(value=None, unit=None, sources=[], quote=None, confident=False))
    invented = response(reply(value=5))
    client = FakeClient([ok, wrong, null, invented, RuntimeError("x")])
    rows = [(8849, True), (8849, True), (8849, True), (None, True), (8849, True)]
    records = [
        run_row(client, settings(), i, {}, expected, True, 10, None)
        for i, (expected, _) in enumerate(rows)
    ]
    summary = summarize(records, settings(), Prices(0.01, 0.1, 0.5), True)
    totals = summary["totals"]
    assert (totals["answered"], totals["nulls"], totals["transport_errors"]) == (3, 1, 1)
    assert totals["search_calls"] == 4
    against = totals["against_expected"]
    assert against["rows_with_expected_value"] == 4
    assert against["coverage"] == 0.5
    assert against["precision_when_answered"] == 0.5
    assert against["false_answer"] == 1
    assert summary["prices_used"]["per_search"] == 0.01


# --- command line ------------------------------------------------------------------

ARGS = ["--question", "Find the height.", "--answer", "number", "--unit", "m"]


def test_dry_run_sends_nothing_and_needs_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    book = probe_workbook(tmp_path / "p.xlsx")
    client = FakeClient([])
    code = run(
        [
            "--workbook",
            str(book),
            *ARGS,
            "--expected-column",
            "expected",
            "--dry-run",
            "--out",
            str(tmp_path / "out"),
        ],
        client=client,
    )
    output = capsys.readouterr().out
    assert code == 0 and client.calls == []
    assert not (tmp_path / "out").exists()
    assert "- Mountain: Everest" in output and "expected" not in output.split("Row:")[1]
    assert "invented" not in output  # empty cells and other rows are not part of row one


def test_the_expected_column_is_never_sent_and_empty_cells_are_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    book = probe_workbook(tmp_path / "p.xlsx")
    client = FakeClient([response(reply()), response(reply(value=None, unit=None, sources=[]))])
    run(
        [
            "--workbook",
            str(book),
            *ARGS,
            "--expected-column",
            "expected",
            "--out",
            str(tmp_path / "out"),
        ],
        client=client,
    )
    first, second = (c["input"] for c in client.calls)
    assert "8849" not in first and "expected" not in first and "note" not in first
    assert "- note: invented" in second


def test_the_key_never_reaches_the_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    book = probe_workbook(tmp_path / "p.xlsx")
    client = FakeClient([RuntimeError(f"bad key {KEY}"), response(reply(value=None, unit=None))])
    run(
        [
            "--workbook",
            str(book),
            *ARGS,
            "--expected-column",
            "expected",
            "--out",
            str(tmp_path / "out"),
        ],
        client=client,
    )
    printed = capsys.readouterr().out
    files = list((tmp_path / "out").glob("*/*"))
    assert {f.name for f in files} == {"rows.jsonl", "summary.json"}
    assert KEY not in printed
    assert all(KEY not in f.read_text(encoding="utf-8") for f in files)
    rows = [json.loads(line) for line in (files[0].parent / "rows.jsonl").read_text().splitlines()]
    assert [r["status"] for r in rows] == ["error", "null"]
