"""The usage log counts events per visitor and refuses anything that could be content."""

import json
import logging

import pytest

from ai_data_enrichment.usage import RECENT_EVENTS, VISITOR, UsageLog


def as_visitor(label: str):
    return VISITOR.set(label)


def test_events_add_up_per_visitor() -> None:
    log = UsageLog()
    token = as_visitor("guest01")
    try:
        log.record("visit")
        log.record("upload", kilobytes=12, sheets=1, rows=10)
        log.record("example", sample="capitals")
        log.record("plan", source="model", status="ready", valid=True, attempts=1, steps=5)
        log.record("plan_failed", attempts=3)
        log.record(
            "model",
            purpose="plan",
            model="gpt-6-luna",
            requests=1,
            input_tokens=1200,
            output_tokens=300,
        )
        log.record(
            "model",
            purpose="task_ideas",
            model="gpt-6-luna",
            requests=1,
            input_tokens=400,
            output_tokens=None,
        )
        log.record("run_started", resume=False, mode="live")
        log.record(
            "run_finished",
            status="needs_review",
            rows=10,
            rows_filled=8,
            seconds=4.2,
            source_requests={"open-meteo": 11, "openrouteservice": 8},
            taken_over=0,
        )
        log.record(
            "run_finished",
            status="completed",
            rows=10,
            rows_filled=10,
            seconds=1.0,
            source_requests={"open-meteo": 4, "openrouteservice": 2},
            taken_over=19,
        )
        log.record("download", artifact="result")
    finally:
        VISITOR.reset(token)
    log.record("visit")

    summary = log.summary()
    assert summary["events"]["run_finished"] == 2 and summary["events"]["visit"] == 2
    by_label = {visitor["who"]: visitor for visitor in summary["visitors"]}
    assert set(by_label) == {"guest01", "local"}
    guest = by_label["guest01"]
    assert {
        key: guest[key]
        for key in (
            "visits",
            "uploads",
            "examples",
            "plans",
            "plans_failed",
            "runs",
            "rows",
            "rows_filled",
            "downloads",
            "model_requests",
            "input_tokens",
            "output_tokens",
        )
    } == {
        "visits": 1,
        "uploads": 1,
        "examples": 1,
        "plans": 1,
        "plans_failed": 1,
        "runs": 1,
        "rows": 20,
        "rows_filled": 18,
        "downloads": 1,
        "model_requests": 2,
        "input_tokens": 1600,
        "output_tokens": 300,
    }
    assert guest["source_requests"] == {"open-meteo": 15, "openrouteservice": 10}
    assert guest["first_seen"] <= guest["last_seen"]
    assert by_label["local"]["visits"] == 1
    # Newest first, each with who and when.
    assert [event["kind"] for event in summary["recent"]][:2] == ["visit", "download"]
    assert summary["recent"][0]["who"] == "local" and summary["recent"][1]["who"] == "guest01"
    assert summary["started_at"] <= summary["now"]


@pytest.mark.parametrize(
    "details",
    [
        {"instruction": "Find the elevation of each mountain"},
        {"filename": "My clients 2026.xlsx"},
        {"name": "x" * 65},
        {"city": "Київ"},
        {"columns": ["Capital From"]},
        {"sources": {"open meteo": 1}},
        {"sources": {"open-meteo": "eleven requests"}},
        {"nested": {"a": {"b": 1}}},
        {"blob": b"bytes"},
    ],
)
def test_text_that_could_be_content_is_refused(details) -> None:
    log = UsageLog()
    with pytest.raises(TypeError):
        log.record("upload", **details)
    with pytest.raises(TypeError):
        log.record("two words")
    assert log.summary()["events"] == {} and log.summary()["recent"] == []


def test_totals_outlive_the_list_of_recent_events() -> None:
    log = UsageLog()
    for _ in range(RECENT_EVENTS + 25):
        log.record("visit")
    summary = log.summary()
    assert len(summary["recent"]) == RECENT_EVENTS
    assert summary["events"] == {"visit": RECENT_EVENTS + 25}
    assert summary["visitors"][0]["visits"] == RECENT_EVENTS + 25


def test_each_event_is_also_one_json_line_where_asked(capsys) -> None:
    quiet = UsageLog()
    quiet.record("visit")
    assert capsys.readouterr().out == ""

    lines = logging.getLogger("enrichment.usage")
    before = list(lines.handlers)
    try:
        log = UsageLog(emit=True)
        token = as_visitor("guest02")
        try:
            log.record("run_finished", status="completed", rows=3, source_requests={"wikidata": 6})
        finally:
            VISITOR.reset(token)
        out = capsys.readouterr().out.strip()
    finally:
        for handler in list(lines.handlers):
            if handler not in before:
                lines.removeHandler(handler)
    assert out.startswith("usage {")
    event = json.loads(out.removeprefix("usage "))
    assert event["who"] == "guest02" and event["kind"] == "run_finished"
    assert event["source_requests"] == {"wikidata": 6} and event["rows"] == 3
