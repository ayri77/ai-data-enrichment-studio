"""The probe's columns mode: dropped links in the output, and --repeat; fake API only."""

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.experiment_web_lookup import run
from tests.support.web_lookup import PAGE, PAGE_2, FakeApi, StatusError, response, slot
from tests.unit.test_experiment_web_lookup_columns import (
    HEADERS,
    KEY,
    QUESTION,
    esim,
    fails,
    make_book,
)

TARGETS_4 = [
    "--target",
    "Battery_mAh:number:mAh",
    "--target",
    "IP_Rating:text",
    "--target",
    "eSIM:yes_no",
    "--target",
    "Notes:text",
]
ROWS: list[list[Any]] = [
    ["Apple", "A1", "EU", 2024, None, None, None, None],
    ["Apple", "A2", "EU", 2023, None, None, None, None],
]


def args_for(book: Path, out: Path, *extra: str, targets: list[str] | None = None) -> list[str]:
    return [
        "--workbook",
        str(book),
        "--context-columns",
        "Brand,Model,Region,Year",
        "--question",
        QUESTION,
        *(targets or TARGETS_4),
        "--out",
        str(out),
        *extra,
    ]


def reply4(
    battery: float | None = 3100, ip: str | None = "IP68", sim: bool | None = True, basis="stated"
) -> dict[str, Any]:
    return {
        "value_1": slot(value=battery, unit="mAh", basis=basis, sources=[PAGE])
        if battery is not None
        else slot(unit=None),
        "value_2": slot(value=ip, basis="concluded", sources=[PAGE_2]) if ip else slot(),
        "value_3": esim(sim),
        "value_4": slot(),
    }


def outputs(out: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[Path]]:
    files = sorted(out.glob("*/*"))
    summary = json.loads((files[0].parent / "summary.json").read_text(encoding="utf-8"))
    lines = (files[0].parent / "rows.jsonl").read_text(encoding="utf-8").splitlines()
    return summary, [json.loads(line) for line in lines], files


# --- dropped links ----------------------------------------------------------------------


def test_dropped_links_in_rows_console_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    book = make_book(tmp_path / "p.xlsx", [ROWS[0], ROWS[1], ROWS[1]])
    lost = {
        "value_1": slot(
            value=1,
            unit="mAh",
            basis="stated",
            sources=["https://invented.example/a", "ftp://x"],
        ),
        "value_2": slot(value="a", basis="stated", sources=["https://invented.example/a"]),
        "value_3": slot(
            value=True,
            basis="stated",
            sources=[f"https://invented.example/e{i}" for i in range(6)],
        ),
        "value_4": slot(),
    }
    partly = reply4()
    partly["value_1"]["sources"] = [PAGE, "https://invented.example/kept-but-extra"]
    partly["value_2"] = slot(value="IP68", basis="stated", sources=["https://invented.example/ip"])
    api = FakeApi(response(lost), response(partly), response(reply4()))
    assert run(args_for(book, tmp_path / "out"), client=api) == 0
    printed = capsys.readouterr().out
    summary, rows, files = outputs(tmp_path / "out")

    assert rows[0]["status"] == "NOTHING_FOUND"
    assert rows[0]["targets"]["Battery_mAh"]["dropped"] == [
        ["https://invented.example/a", "not_reported"],
        ["ftp://x", "not_a_link"],
    ]
    assert rows[0]["targets"]["Battery_mAh"]["diagnostic"]["links_dropped"] == 2
    assert rows[1]["targets"]["Battery_mAh"]["dropped"] == [
        ["https://invented.example/kept-but-extra", "not_reported"]
    ]
    assert rows[2]["targets"]["Battery_mAh"]["dropped"] == []

    lines = printed.splitlines()
    first = lines.index(next(line for line in lines if line.startswith("[row 2] NOTHING_FOUND")))
    shown = []
    for line in lines[first + 1 :]:
        if not line.startswith("    dropped: "):
            break
        shown.append(line)
    # distinct, once each, at most five, in order of appearance
    assert shown == [
        "    dropped: not_reported https://invented.example/a",
        "    dropped: not_a_link ftp://x",
        "    dropped: not_reported https://invented.example/e0",
        "    dropped: not_reported https://invented.example/e1",
        "    dropped: not_reported https://invented.example/e2",
    ]
    # A kept slot's dropped link is not printed; a slot that lost every link is.
    assert "kept-but-extra" not in printed
    assert "    dropped: not_reported https://invented.example/ip" in printed
    assert printed.count("    dropped: ") == 6

    assert summary["links"]["dropped_total"] == 2 + 1 + 6 + 1 + 1
    assert summary["links"]["dropped_not_reported"] == 10
    assert summary["links"]["dropped_not_a_link"] == 1
    assert summary["links"]["dropped_duplicate"] == 0
    assert summary["links"]["dropped_over_limit"] == 0
    assert "Links dropped: 11 (not reported by the search 10, not a link 1" in printed
    assert "stability" not in summary and ", ask " not in printed
    for text in (printed, *(f.read_text(encoding="utf-8") for f in files)):
        assert KEY not in text


# --- --repeat ---------------------------------------------------------------------------


def repeat_replies() -> list[Any]:
    return [
        # row 2: battery same (basis differs), IP two values, eSIM missing once
        response(reply4(ip="IP68", basis="stated")),
        response(reply4(ip="IP67", sim=None, basis="concluded")),
        response(reply4(ip=" ip68 ", basis="stated")),
        # sheet row 3: the second ask fails
        response(reply4()),
        StatusError(500, f"boom {KEY}"),
        response(reply4()),
    ]


def test_repeat_reports_stability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    book = make_book(tmp_path / "p.xlsx", ROWS)
    api = FakeApi(*repeat_replies())
    assert run(args_for(book, tmp_path / "out", "--repeat", "3"), client=api) == 0
    printed = capsys.readouterr().out
    summary, rows, files = outputs(tmp_path / "out")

    assert len(api.calls) == 6
    assert printed.index("This run will make 6 requests (2 rows x 3 asks)") < printed.index(
        "[row 2, ask 1/3]"
    )
    for row, ask in ((2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3)):
        assert f"[row {row}, ask {ask}/3]" in printed
    assert [(r["row"], r["ask"]) for r in rows] == [(2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3)]
    assert [r["status"] for r in rows] == ["ok"] * 4 + ["PROVIDER_ERROR", "ok"]

    stability = summary["stability"]
    assert stability["asks_per_row"] == 3 and stability["rows_compared"] == 2
    assert stability["rows_not_completed"] == 0 and stability["slots"] == 8
    assert stability["always_the_same_value"] == 1  # row 2 battery
    assert stability["values_differ"] == 1  # row 2 IP
    assert stability["sometimes_missing"] == 4  # row 2 eSIM; row 3 battery, IP, eSIM
    assert stability["never_found"] == 2  # Notes in both rows
    assert stability["values_differ_detail"] == [
        {
            "row": 2,
            "target": "IP_Rating",
            "asks": 3,
            "distinct_values": [{"value": "IP68", "count": 2}, {"value": "IP67", "count": 1}],
        }
    ]
    assert stability["rows_failed_in_some_asks"] == {"rows": 1, "by_code": {"PROVIDER_ERROR": 1}}
    # found in all three asks: row 2 battery and IP; only the battery's basis changed
    assert stability["found_in_all_asks"] == {
        "slots": 2,
        "basis_differed_between_asks": 1,
    }
    assert rows[0]["targets"]["IP_Rating"]["stability"]["category"] == "values_differ"
    assert rows[0]["targets"]["IP_Rating"]["stability"]["distinct_values"][0]["count"] == 2
    assert rows[4]["targets"]["Notes"]["stability"]["category"] == "never_found"

    assert "  row 2, IP_Rating: 2 different values in 3 asks" in printed
    assert printed.count("different values in 3 asks") == 1
    assert (
        "always the same value 1 | values differ 1 | sometimes missing 4 | never found 2" in printed
    )
    assert "Rows that failed in some asks but not all: 1 {'PROVIDER_ERROR': 1}" in printed
    assert "the basis differed between asks (stated in one, concluded in another) for 1" in printed

    # usage, time and cost cover all asks; per-row figures are per ask
    assert summary["usage"] == {
        "requests": 6,
        "input_tokens": 5000,
        "output_tokens": 1000,
        "web_searches": 10,
    }
    assert summary["rows"]["planned"] == 6 and summary["rows"]["asked"] == 6
    assert summary["rows"]["succeeded"] == 5
    assert summary["settings"]["repeat"] == 3
    assert summary["estimated_cost"]["per_row"] == round(summary["estimated_cost"]["total"] / 6, 4)
    assert summary["seconds_per_row"] == round(summary["seconds_total"] / 6, 2)
    for text in (printed, *(f.read_text(encoding="utf-8") for f in files)):
        assert KEY not in text


def test_at_most_ten_differing_slots_are_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    book = make_book(tmp_path / "p.xlsx", [ROWS[0]] * 12)
    replies = [response(reply4(ip=ip)) for _ in range(12) for ip in ("IP67", "IP68")]
    run(args_for(book, tmp_path / "out", "--repeat", "2"), client=FakeApi(*replies))
    printed = capsys.readouterr().out
    assert printed.count("2 different values in 2 asks") == 10
    assert "... and 2 more in summary.json" in printed
    summary, _, _ = outputs(tmp_path / "out")
    assert summary["stability"]["values_differ"] == 12
    assert len(summary["stability"]["values_differ_detail"]) == 12


def test_a_refused_request_stops_every_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    book = make_book(tmp_path / "p.xlsx", ROWS)
    api = FakeApi(StatusError(400, f"Unsupported parameter. key={KEY}"))
    assert run(args_for(book, tmp_path / "out", "--repeat", "3"), client=api) == 0
    printed = capsys.readouterr().out
    summary, rows, files = outputs(tmp_path / "out")
    assert len(api.calls) == 1 and len(rows) == 1
    assert "This run will make 6 requests" in printed
    assert "Stopped after row 2, ask 1/3: REQUEST_REFUSED" in printed
    assert summary["stability"]["rows_compared"] == 0
    assert summary["stability"]["rows_not_completed"] == 1
    for text in (printed, *(f.read_text(encoding="utf-8") for f in files)):
        assert KEY not in text


def test_the_ten_requests_per_row_ceiling_and_the_single_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = make_book(tmp_path / "p.xlsx", ROWS)
    for bad in ("0", "11", "-1"):
        err = fails(args_for(book, tmp_path / "o", "--repeat", bad), capsys)
        assert "--repeat must be between 1 and 10" in err
    single = [
        "--workbook",
        str(book),
        "--question",
        QUESTION,
        "--out",
        str(tmp_path / "o"),
    ]
    err = fails([*single, "--repeat", "2"], capsys)
    assert "--repeat only applies in columns mode" in err
    # the default value is accepted there
    assert run([*single, "--repeat", "1", "--dry-run"], client=FakeApi()) == 0
    assert HEADERS[0] in capsys.readouterr().out


def test_a_single_ask_prints_the_old_row_mark_and_no_stability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    book = make_book(tmp_path / "p.xlsx", ROWS[:1])
    run(args_for(book, tmp_path / "out"), client=FakeApi(response(reply4())))
    printed = capsys.readouterr().out
    assert "[row 2] ok" in printed and ", ask " not in printed
    assert "This run will make 1 request (1 row x 1 ask)" in printed
    summary, rows, _ = outputs(tmp_path / "out")
    assert "stability" not in summary and rows[0]["ask"] == 1
