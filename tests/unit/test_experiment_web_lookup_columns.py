"""The probe's columns mode: targets, dry run, a full run against a fake API; no live calls."""

import json
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook
from scripts.experiment_web_lookup import compare_existing, parse_target, parse_yes_no, run
from tests.support.web_lookup import PAGE, PAGE_2, FakeApi, StatusError, response, slot

from ai_data_enrichment.providers.web_lookup import LookupColumn, LookupTask, build_request

KEY = "sk-test-secret-0123456789"
QUESTION = "Fill the technical characteristics."
HEADERS = ["Brand", "Model", "Region", "Year", "Battery_mAh", "IP_Rating", "eSIM", "Notes"]
TARGETS = [
    "--target",
    "Battery_mAh:number:mAh",
    "--target",
    "IP_Rating:text",
    "--target",
    "eSIM:yes_no",
]


def base_args(book: Path, out: Path, *extra: str) -> list[str]:
    return [
        "--workbook",
        str(book),
        "--context-columns",
        "Brand,Model,Region,Year",
        "--question",
        QUESTION,
        *TARGETS,
        "--out",
        str(out),
        *extra,
    ]


def make_book(path: Path, rows: list[list[Any]]) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def reply(v1: dict[str, Any], v2: dict[str, Any], v3: dict[str, Any]) -> dict[str, Any]:
    return {"value_1": v1, "value_2": v2, "value_3": v3}


def battery(**changes: Any) -> dict[str, Any]:
    return slot(value=3100, unit="mAh", basis="stated", sources=[PAGE], quote="3100 mAh", **changes)


def ip(value: str | None = "IP68", **changes: Any) -> dict[str, Any]:
    if value is None:
        return slot()
    return slot(value=value, basis="concluded", sources=[PAGE_2], **changes)


def esim(value: bool | None = True) -> dict[str, Any]:
    return slot() if value is None else slot(value=value, basis="stated", sources=[PAGE_2])


def null_battery() -> dict[str, Any]:
    return slot(unit=None)


# --- targets ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("Battery:number:mAh", ("Battery", "number", "mAh")),
        ("Battery:number", ("Battery", "number", None)),
        ("IP Rating:text", ("IP Rating", "text", None)),
        ("eSIM:yes_no", ("eSIM", "yes_no", None)),
        ("Size: large:text", ("Size: large", "text", None)),  # split from the right
        ("Weight (g: net):number:g", ("Weight (g: net)", "number", "g")),
    ],
)
def test_target_parsing(spec: str, expected: tuple[str, str, str | None]) -> None:
    assert parse_target(spec) == expected


@pytest.mark.parametrize(
    "spec", ["Name", "Name:color", "Name:text:cm", "Name:yes_no:x", "Name:number:", ":text"]
)
def test_bad_targets_are_refused(spec: str) -> None:
    with pytest.raises(ValueError, match="--target"):
        parse_target(spec)


def test_yes_no_words_in_three_languages() -> None:
    for word in ("Yes", " TRUE ", "y", "1", "Да", "так", "Ja", True, 1):
        assert parse_yes_no(word) is True
    for word in ("No", "false", "N", "0", "ні", "Нет", "NEIN", False, 0):
        assert parse_yes_no(word) is False
    for word in ("maybe", "yes please", "2", "ja, aber"):
        assert parse_yes_no(word) is None


def test_comparison_rules() -> None:
    assert compare_existing("number", 3000, 3100.0, 10) == "agree"
    assert compare_existing("number", 3000, 3400.0, 10) == "differ"
    assert compare_existing("number", "n/a", 3400.0, 10) == "not_comparable"
    assert compare_existing("text", " ip68 ", "IP68", 10) == "agree"
    assert compare_existing("text", "IP68", "IP68 water resistant", 10) == "differ"
    assert compare_existing("yes_no", "ja", "Yes", 10) == "agree"
    assert compare_existing("yes_no", "nein", "Yes", 10) == "differ"
    assert compare_existing("yes_no", "maybe", "Yes", 10) == "not_comparable"
    assert compare_existing("text", "IP68", None, 10) == "nothing_found"
    assert compare_existing("text", None, "IP68", 10) is None
    assert compare_existing("text", "  ", None, 10) is None


# --- option errors ----------------------------------------------------------------------


@pytest.fixture
def book(tmp_path: Path) -> Path:
    return make_book(
        tmp_path / "p.xlsx", [["Apple", "iPhone 15", "EU", 2024, None, None, None, "x"]]
    )


def fails(args: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as stop:
        run(args, client=FakeApi())
    assert stop.value.code == 2
    return capsys.readouterr().err


def test_unknown_target_header_lists_the_headers(
    book: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = base_args(book, tmp_path / "o")
    args[args.index("IP_Rating:text")] = "Nope:text"
    err = fails(args, capsys)
    assert "'Nope'" in err and "Battery_mAh" in err


def test_unit_with_text_and_duplicates_and_context_overlap(
    book: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    common = ["--workbook", str(book), "--question", QUESTION, "--out", str(tmp_path / "o")]
    err = fails([*common, "--context-columns", "Brand", "--target", "IP_Rating:text:cm"], capsys)
    assert "unit is only allowed with the kind number" in err
    err = fails(
        [
            *common,
            "--context-columns",
            "Brand",
            "--target",
            "IP_Rating:text",
            "--target",
            "IP_Rating:text",
        ],
        capsys,
    )
    assert "not valid" in err and "unique" in err
    err = fails(
        [*common, "--context-columns", "Brand,IP_Rating", "--target", "IP_Rating:text"], capsys
    )
    assert "also a context column" in err
    err = fails([*common, "--context-columns", "Nope", "--target", "IP_Rating:text"], capsys)
    assert "Unknown --context-columns" in err
    err = fails([*common, "--target", "IP_Rating:text"], capsys)
    assert "--context-columns is required" in err


def test_mixing_with_the_single_value_mode_is_refused(
    book: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for extra in (["--answer", "text"], ["--unit", "m"], ["--expected-column", "Notes"]):
        err = fails(base_args(book, tmp_path / "o", *extra), capsys)
        assert extra[0] in err and "single-value mode" in err
    err = fails(base_args(book, tmp_path / "o", "--max-tool-calls", "5"), capsys)
    assert "--max-tool-calls cannot be changed" in err


# --- dry run ----------------------------------------------------------------------------


def test_dry_run_prints_the_exact_request_and_builds_no_client(
    book: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import openai

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("a client was constructed")

    monkeypatch.setattr(openai, "OpenAI", forbidden)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    api = FakeApi()
    assert run(base_args(book, tmp_path / "o", "--dry-run"), client=api) == 0
    assert api.calls == [] and not (tmp_path / "o").exists()
    output = capsys.readouterr().out
    printed = json.loads(output[output.index("{") :])
    task = LookupTask(
        question=QUESTION,
        context=(("ctx_1", "Brand"), ("ctx_2", "Model"), ("ctx_3", "Region"), ("ctx_4", "Year")),
        columns=(
            LookupColumn(slot="value_1", header="Battery_mAh", kind="number", unit="mAh"),
            LookupColumn(slot="value_2", header="IP_Rating", kind="text"),
            LookupColumn(slot="value_3", header="eSIM", kind="yes_no"),
        ),
    )
    context = {"Brand": "Apple", "Model": "iPhone 15", "Region": "EU", "Year": "2024"}
    wanted = build_request(task, context, model="gpt-6-luna", reasoning_effort="low")
    assert printed == {**wanted, "timeout": 90.0}


# --- a full run -------------------------------------------------------------------------


def long_quote_ip() -> dict[str, Any]:
    return ip("IP68", quote="q" * 400, sources=[PAGE_2, "https://unreported.example/x"])


ROWS: list[list[Any]] = [
    ["Apple", "A1", "EU", 2024, 3000, " ip68 ", "ja", None],
    ["Apple", "A2", "EU", 2024, None, None, None, None],
    ["Apple", "A3", "US", 2023.0, 5000, "IP67", "nein", None],
    ["Apple", "A4", "US", 2022, None, None, "maybe", None],
    ["Apple", "A5", "EU", 2021, None, None, "ні", None],
    ["Apple", "A6", None, 2020, None, None, "No", None],
]


def full_replies() -> list[Any]:
    long_ip = slot(
        value="IP68",
        basis="concluded",
        sources=[PAGE_2, "https://unreported.example/x"],
        quote="q" * 400,
    )
    return [
        response(reply(battery(), long_ip, esim(True))),
        StatusError(500, f"boom {KEY}"),
        response(reply(battery(), ip(None), esim(True))),
        response(reply(null_battery(), ip("IP68"), esim(False))),
        response(
            reply(battery(), ip("IP68"), esim(False)),
            reported=(PAGE + "?ref=a", PAGE_2),
        ),
        response(reply(null_battery(), ip(None), esim(None))),
    ]


def read_outputs(out: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[Path]]:
    files = sorted(out.glob("*/*"))
    summary = json.loads((out / files[0].parent.name / "summary.json").read_text(encoding="utf-8"))
    lines = (files[0].parent / "rows.jsonl").read_text(encoding="utf-8").splitlines()
    return summary, [json.loads(line) for line in lines], files


def test_full_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    book = make_book(tmp_path / "p.xlsx", ROWS)
    before = book.read_bytes()
    api = FakeApi(*full_replies())
    assert run(base_args(book, tmp_path / "out", "--concurrency", "3"), client=api) == 0
    printed = capsys.readouterr().out
    summary, rows, files = read_outputs(tmp_path / "out")

    assert book.read_bytes() == before
    assert len(api.calls) == 6
    assert {f.name for f in files} == {"rows.jsonl", "summary.json"}
    # context: empty cells are left out, numbers are sent as numbers
    assert "- Region" not in api.calls[5]["input"]
    assert "- Year: 2023" in api.calls[2]["input"]
    assert "timeout" in api.calls[0]

    assert [r["status"] for r in rows] == [
        "ok", "PROVIDER_ERROR", "ok", "ok", "ok", "NOTHING_FOUND",
    ]  # fmt: skip
    first = rows[0]["targets"]
    assert first["Battery_mAh"]["agrees"] is True and first["Battery_mAh"]["existing"] == 3000
    assert first["IP_Rating"]["agrees"] is True
    assert first["IP_Rating"]["diagnostic"]["links_dropped"] == 1
    assert first["IP_Rating"]["diagnostic"]["quote_truncated"] is True
    assert first["IP_Rating"]["basis"] == "concluded" and first["IP_Rating"]["links"] == [PAGE_2]
    assert first["eSIM"]["value"] == "Yes" and first["eSIM"]["agrees"] is True
    assert rows[2]["targets"]["Battery_mAh"]["agrees"] is False
    assert rows[2]["targets"]["IP_Rating"]["diagnostic"]["reason"] == "no_value"
    assert rows[2]["targets"]["IP_Rating"]["comparison"] == "nothing_found"
    assert rows[2]["targets"]["eSIM"]["agrees"] is False
    assert rows[3]["targets"]["eSIM"]["comparison"] == "not_comparable"
    assert rows[4]["targets"]["eSIM"]["agrees"] is True
    assert rows[4]["targets"]["Battery_mAh"]["diagnostic"]["link_matches"] == ["address"]
    assert rows[5]["targets"]["eSIM"]["comparison"] == "nothing_found"
    assert "[key]" in rows[1]["http_error"]

    assert summary["rows"] == {
        "planned": 6,
        "asked": 6,
        "succeeded": 4,
        "failed_by_code": {"PROVIDER_ERROR": 1, "NOTHING_FOUND": 1},
        "stopped_early": None,
    }
    assert summary["slots"] == {"found": 10, "not_found": 5, "not_found_by_reason": {"no_value": 5}}
    assert summary["found_by_basis"] == {"stated": 7, "concluded": 3}
    assert summary["links"] == {
        "kept_exact": 9,
        "kept_address": 1,
        "dropped_not_reported_by_search": 1,
        "quotes_truncated": 1,
    }
    assert summary["comparison_with_existing"] == {
        "compared": 6,
        "agree": 4,
        "differ": 2,
        "not_comparable": 1,
        "existing_but_nothing_found": 2,
    }
    assert summary["usage"] == {
        "requests": 6,
        "input_tokens": 5000,
        "output_tokens": 1000,
        "web_searches": 10,
    }
    assert summary["estimated_cost"] == {
        "total": 0.101,
        "per_row": 0.0168,
        "per_found_value": 0.0101,
    }
    settings = summary["settings"]
    assert settings["configuration_id"] == "openai-web-lookup-p1-gpt-6-luna-low"
    assert settings["targets"][0] == {"header": "Battery_mAh", "kind": "number", "unit": "mAh"}
    assert LookupTask.from_json(summary["task_json"]).question == QUESTION

    assert printed.count("API error text") == 1
    assert (
        "[row 2] ok | Battery_mAh: 3100 (stated, 1 link) [agrees with the cell] | IP_Rating: IP68"
        in printed
    )
    assert "IP_Rating: — (no_value)" in printed
    assert "[row 3] PROVIDER_ERROR | The web search request failed." in printed
    assert "--concurrency is ignored" in printed
    assert "do not commit them" in printed and "one observation" in printed
    assert "not compared with the bill" in printed
    for text in (printed, *(f.read_text(encoding="utf-8") for f in files)):
        assert KEY not in text


def test_a_refused_request_stops_the_run_and_shows_the_redacted_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    book = make_book(tmp_path / "p.xlsx", ROWS)
    api = FakeApi(StatusError(400, f"Unsupported parameter: max_tool_calls. key={KEY}"))
    assert run(base_args(book, tmp_path / "out"), client=api) == 0
    printed = capsys.readouterr().out
    summary, rows, files = read_outputs(tmp_path / "out")
    assert len(api.calls) == 1 and len(rows) == 1
    assert rows[0]["status"] == "REQUEST_REFUSED"
    assert "Unsupported parameter: max_tool_calls. key=[key]" in printed
    assert "Stopped after row 2: REQUEST_REFUSED" in printed
    assert "--dry-run" in printed
    assert summary["rows"]["asked"] == 1 and summary["rows"]["stopped_early"] == "REQUEST_REFUSED"
    assert summary["rows"]["planned"] == 6
    for text in (printed, *(f.read_text(encoding="utf-8") for f in files)):
        assert KEY not in text


def test_access_denied_stops_the_run_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    book = make_book(tmp_path / "p.xlsx", ROWS)
    api = FakeApi(StatusError(401))
    run(base_args(book, tmp_path / "out"), client=api)
    assert len(api.calls) == 1
    assert "Stopped after row 2: SOURCE_ACCESS_DENIED" in capsys.readouterr().out
