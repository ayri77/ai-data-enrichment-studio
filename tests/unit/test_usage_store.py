"""Usage kept in a file: replayed through the same counting, compacted, never fatal."""

import json
import logging
import threading
from pathlib import Path

import pytest

from ai_data_enrichment import usage as usage_module
from ai_data_enrichment.usage import (
    RECENT_EVENTS,
    VISITOR,
    FileUsageStore,
    UsageLog,
    parse_usage_file,
    store_for,
)


def kept(path: Path, **options) -> UsageLog:
    return UsageLog(store=FileUsageStore(path, **options))


def restarted(summary: dict) -> dict:
    """What a summary says of the data, without what is a property of the process."""
    ignored = {"now", "started_at", "process_started_at", "persistent", "reason"}
    data = {k: v for k, v in summary.items() if k not in ignored}
    # Visitors seen in the same second have no order of their own.
    return {**data, "visitors": sorted(data["visitors"], key=lambda v: v["who"])}


def some_events(log: UsageLog) -> None:
    token = VISITOR.set("guest01")
    try:
        log.record("visit")
        log.record("upload", kilobytes=12, sheets=1, rows=10)
        log.record("example", sample="capitals")
        log.record("plan", source="model", status="ready", valid=True, attempts=1, steps=5)
        log.record("plan_failed", attempts=3)
        log.record(
            "model", purpose="plan", model="gpt-6-luna", requests=1, input_tokens=1200,
            output_tokens=300,
        )  # fmt: skip
        log.record(
            "model", purpose="web_lookup", model="gpt-6-luna", requests=4, input_tokens=900,
            output_tokens=80, web_searches=4,
        )  # fmt: skip
        log.record(
            "task_ideas", language="en", again=0, columns=0, proposed=3, too_long=0,
            incomplete=0, internal_names=1, shown=2,
        )  # fmt: skip
        log.record("run_started", resume=False, mode="live")
        log.record(
            "run_finished", status="needs_review", rows=10, rows_filled=8, seconds=4.2,
            source_requests={"open-meteo": 11, "openrouteservice": 8}, taken_over=0,
        )  # fmt: skip
        log.record("download", artifact="result")
    finally:
        VISITOR.reset(token)
    log.record("visit")


def test_a_second_log_over_the_file_gives_the_summary_of_the_first(tmp_path: Path) -> None:
    path = tmp_path / "usage" / "events.jsonl"
    first = kept(path)
    some_events(first)
    before = first.summary()
    assert before["persistent"] is True and "reason" not in before
    assert before["since"] == before["recent"][-1]["at"]

    again = kept(path)
    after = again.summary()
    assert restarted(after) == restarted(before)
    # Newest first, in the order they happened.
    assert [e["kind"] for e in after["recent"]][:3] == ["visit", "download", "run_finished"]
    guest = next(v for v in after["visitors"] if v["who"] == "guest01")
    assert guest["web_searches"] == 4 and guest["model_requests"] == 5
    assert after["events"]["task_ideas"] == 1
    assert after["skipped_lines"] == 0
    # The new process goes on counting on the old totals.
    again.record("visit")
    assert kept(path).summary()["events"]["visit"] == 3


def test_each_line_is_one_json_object_holding_what_the_event_holds(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = kept(path)
    some_events(log)
    lines = path.read_text("utf-8").splitlines()
    assert [json.loads(line) for line in lines] == list(reversed(log.summary()["recent"]))
    assert all(set(json.loads(line)) >= {"at", "who", "kind"} for line in lines)


def test_without_a_store_the_log_is_as_before(tmp_path: Path) -> None:
    log = UsageLog()
    log.record("visit")
    summary = log.summary()
    assert (summary["persistent"], summary["reason"]) == (False, "off")
    assert summary["skipped_lines"] == 0 and summary["since"] == summary["recent"][0]["at"]
    assert summary["started_at"] == summary["process_started_at"]
    assert UsageLog().summary()["since"] is None


def test_malformed_and_disallowed_lines_are_skipped_and_counted(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    good = {"at": "2026-11-14T09:00:00+00:00", "who": "guest01", "kind": "upload", "rows": 3}
    lines = [
        json.dumps(good),
        "this is not json",
        '{"at": "2026-11-14T09:01:00+00:00", "who": "guest01", "kind": "upload"',  # cut off
        json.dumps({**good, "filename": "My clients 2026.xlsx"}),  # text with spaces
        json.dumps({**good, "city": "Київ"}),  # not ASCII
        json.dumps({**good, "nested": {"a": {"b": 1}}}),
        json.dumps({**good, "kind": "two words"}),
        json.dumps({"who": "guest01", "kind": "visit"}),  # no time
        json.dumps(["not", "an", "event"]),
        json.dumps({**good, "kind": "totals"}),  # only a first record may be one
        json.dumps({**good, "kind": "run_finished", "rows": "many"}),  # does not add up
        json.dumps({**good, "kind": "run_finished", "source_requests": ["x"]}),
        "",  # an empty line is nothing
        json.dumps({**good, "at": "2026-11-14T09:02:00+00:00", "kind": "visit"}),
    ]
    path.write_bytes(("\n".join(lines) + "\n").encode() + b"\xff\xfe broken bytes\n")
    summary = kept(path).summary()
    assert summary["events"] == {"upload": 1, "visit": 1}
    assert summary["skipped_lines"] == 11 + 1
    assert summary["persistent"] is True
    assert "Київ" not in json.dumps(summary) and "clients" not in json.dumps(summary)
    visitor = summary["visitors"][0]
    assert (visitor["uploads"], visitor["visits"], visitor["rows"]) == (1, 1, 0)


def test_a_line_cut_off_by_a_crash_does_not_swallow_the_next_event(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"at": "2026-11-14T09:00:00+00:00", "who": "local", "ki', "utf-8")
    log = kept(path)
    log.record("visit")
    again = kept(path)
    assert again.summary()["events"] == {"visit": 1} and again.summary()["skipped_lines"] == 1


def test_a_write_that_fails_is_reported_once_and_counting_goes_on(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class Broken(FileUsageStore):
        def append(self, event):
            raise OSError("disk full: secret-path-fragment")

    log = UsageLog(store=Broken(tmp_path / "events.jsonl"))
    with caplog.at_level(logging.WARNING):
        log.record("upload", kilobytes=5, sheets=1, rows=3)
        log.record("visit")
    summary = log.summary()
    assert (summary["persistent"], summary["reason"]) == (False, "write_failed")
    assert summary["events"] == {"upload": 1, "visit": 1}
    warnings = [r for r in caplog.records if r.name == usage_module.__name__]
    assert len(warnings) == 1
    # Nothing of the event, and nothing of the error text, is in the warning.
    text = warnings[0].getMessage() + str(warnings[0].args)
    assert "kilobytes" not in text and "secret-path-fragment" not in text
    assert not (tmp_path / "events.jsonl").exists()


def test_a_folder_that_cannot_be_made_degrades_instead_of_failing_the_request(
    tmp_path: Path,
) -> None:
    (tmp_path / "blocked").write_text("a file where the folder should be", "utf-8")
    log = kept(tmp_path / "blocked" / "usage" / "events.jsonl")
    log.record("visit")
    assert log.summary()["reason"] == "write_failed" and log.summary()["events"] == {"visit": 1}
    # And a file that cannot be read at start.
    unreadable = tmp_path / "directory.jsonl"
    unreadable.mkdir()
    started = kept(unreadable)
    started.record("visit")
    assert started.summary()["reason"] == "write_failed"


def test_compaction_keeps_the_totals_the_start_and_the_recent_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = kept(path, limit=15_000)
    plain = UsageLog()
    for number in range(RECENT_EVENTS + 120):
        for target in (log, plain):
            token = VISITOR.set("guest01" if number % 3 else "guest02")
            try:
                target.record("visit")
                if number % 5 == 0:
                    target.record(
                        "run_finished", status="completed", rows=2, rows_filled=1,
                        source_requests={"open-meteo": number},
                    )  # fmt: skip
            finally:
                VISITOR.reset(token)
    # Compacted: a snapshot, then the recent events and what came after them.
    lines = path.read_text("utf-8").splitlines()
    assert json.loads(lines[0])["kind"] == "totals" and json.loads(lines[0])["events"]
    # Fewer lines than events: the oldest were folded into the snapshot.
    assert len(lines) < 504
    assert not path.with_name("events.jsonl.new").exists()

    expected = restarted(plain.summary())
    assert restarted(log.summary()) == expected
    once = kept(path)
    assert restarted(once.summary()) == restarted(log.summary())
    assert once.summary()["skipped_lines"] == 0
    # Totals, not only what is still listed.
    assert once.summary()["events"]["visit"] == RECENT_EVENTS + 120
    # A second restart after more events and another compaction still agrees.
    for _ in range(300):
        once.record("visit")
    twice = kept(path)
    assert restarted(twice.summary()) == restarted(once.summary())
    assert twice.summary()["events"]["visit"] == RECENT_EVENTS + 420
    assert twice.summary()["since"] == plain.summary()["since"]
    assert len(twice.summary()["recent"]) == RECENT_EVENTS


def test_a_snapshot_that_is_not_one_is_skipped_and_counted(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    snapshot = {"kind": "totals", "since": "2026-11-01T00:00:00+00:00", "events": {"visit": "x"}}
    path.write_text(json.dumps(snapshot) + "\n", "utf-8")
    summary = kept(path).summary()
    assert summary["skipped_lines"] == 1 and summary["events"] == {} and summary["since"] is None


def test_a_failed_compaction_keeps_the_old_file_and_degrades(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"

    class NoRewrite(FileUsageStore):
        def rewrite(self, records):
            raise OSError("no space")

    log = UsageLog(store=NoRewrite(path, limit=200))
    for _ in range(10):
        log.record("visit")
    assert log.summary()["reason"] == "write_failed" and log.summary()["events"] == {"visit": 10}
    assert kept(path).summary()["events"]["visit"] >= 1
    assert not path.with_name("events.jsonl.new").exists()


def test_two_threads_recording_at_once_lose_nothing(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = kept(path, limit=30_000)

    def work(label: str) -> None:
        token = VISITOR.set(label)
        try:
            for _ in range(400):
                log.record("visit")
        finally:
            VISITOR.reset(token)

    threads = [threading.Thread(target=work, args=(f"guest0{n}",)) for n in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert log.summary()["events"] == {"visit": 800}
    again = kept(path).summary()
    assert again["events"] == {"visit": 800} and again["skipped_lines"] == 0
    assert {v["who"]: v["visits"] for v in again["visitors"]} == {"guest01": 400, "guest02": 400}


def test_an_event_called_totals_is_refused() -> None:
    with pytest.raises(TypeError):
        UsageLog().record("totals")


def test_the_setting_is_unset_off_or_a_path(tmp_path: Path) -> None:
    assert parse_usage_file(None) is None and parse_usage_file("  ") is None
    assert parse_usage_file("Off") == "off" and parse_usage_file(" OFF ") == "off"
    assert parse_usage_file("/some/where.jsonl") == "/some/where.jsonl"
    assert store_for(None) is None and store_for("off") is None
    store = store_for(str(tmp_path / "x.jsonl"))
    assert isinstance(store, FileUsageStore)
