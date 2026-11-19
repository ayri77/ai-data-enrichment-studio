"""The mail extraction probe end to end on the invented fixtures; a fake API, no network."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from scripts.experiment_mail_extraction import (
    from_text,
    message_document,
    parse_target,
    run,
    write_workbook,
)
from tests.support.text_extraction import entry, response
from tests.support.web_lookup import RowApi, StatusError

from ai_data_enrichment.mail.messages import MailMessage
from ai_data_enrichment.providers.text_extraction import (
    ExtractionColumn,
    ExtractionTask,
    build_request,
)
from ai_data_enrichment.providers.text_extraction_reply import (
    OUTCOME_FOUND,
    ExtractionResult,
    KeptValue,
)

FIXTURES = Path(__file__).parents[2] / "samples" / "probe" / "mail"
KEY = "sk-test-secret-0123456789"
QUESTION = "Payments and subscriptions: what was paid, or what changes"
TARGETS = [
    "--target", "Service:text",
    "--target", "Amount:number",
    "--target", "Paid on:date",
    "--target", "Event:one_of:payment|renewal notice|ended",
]  # fmt: skip


def args(out: Path, *extra: str, eml: Path = FIXTURES) -> list[str]:
    return ["--eml-dir", str(eml), "--task", QUESTION, *TARGETS, "--out-dir", str(out), *extra]


def reply(applies: bool = True, **slots: dict[str, Any]) -> dict[str, Any]:
    values = {f"value_{i}": entry() for i in range(1, 5)}
    values.update(slots)
    return {"applies": applies, "values": values}


def answer_for(prompt: str) -> Any:
    """What a model might say to each fixture."""
    if "Subject: Ihre Rechnung" in prompt:
        return reply(
            value_1=entry("Cloudbox Plus", "Tarif: Cloudbox Plus (2 TB Speicher)"),
            value_2=entry(10.0, "Gesamt: 10,00 EUR"),
            value_3=entry("2026-11-01", "Bezahlt am 01.11.2026"),
            value_4=entry("payment", "Bezahlt am 01.11.2026"),
        )
    if "Subject: Чек Streamly" in prompt:
        return reply(
            value_1=entry("Streamly Premium", "Сервис: Streamly Premium"),
            value_2=entry(399.0, "Стоимость: 399,00 ₽ в месяц"),
            value_3=entry("2026-11-12", "Списано: 12 ноября 2026"),
            value_4=entry("payment", "Списано: 12 ноября 2026"),
        )
    if "Subject: Your receipt from PayRelay" in prompt:
        return reply(
            value_1=entry("Notes App Pro", "Notes App Pro (annual plan)"),
            value_2=entry(29.99, "Amount paid $29.99"),
            value_3=entry("2026-11-03", "Paid on 3 Nov 2026"),
            value_4=entry("payment", "Amount paid $29.99"),
        )
    if "Subject: Your Streamly trial" in prompt:
        return reply(
            value_1=entry("Streamly Premium", "free trial of Streamly Premium"),
            value_2=entry(9.99, "$9.99 per month"),
            value_4=entry("trial ending", "ends on 20 November 2026"),  # not an option
        )
    if "Subject: Payment failed" in prompt:
        return reply(
            value_1=entry("HostHill Starter", "HostHill Starter (4.50 EUR)"),
            value_2=entry(5.0, "HostHill Starter (4.50 EUR)"),  # not in its quote
        )
    if "Subject: Підписку Streamly" in prompt:
        return reply(
            value_1=entry("Streamly Basic", "підписку Streamly Basic завершено"),
            value_3=entry("2026-10-30", "30 жовтня 2026"),
            value_4=entry("ended", "завершено"),
        )
    if "Subject: Price change" in prompt:
        return StatusError(500)
    if "Subject: Receipt for your payment" in prompt:
        return reply(
            value_1=entry("HostHill Starter", "Item: HostHill Starter, monthly"),
            value_2=entry(4.5, "Amount: 4.50 EUR"),
            value_3=entry("2026-11-04", "Date: 2026-11-04"),
            value_4=entry("payment", "Amount: 4.50 EUR"),
        )
    return reply(applies=False)  # the advertisements


def choose(kwargs: dict[str, Any]) -> Any:
    answer = answer_for(kwargs["input"])
    return answer if isinstance(answer, Exception) else response(answer)


def fake() -> RowApi:
    return RowApi(choose, api_key=KEY)


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)


# --- targets ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("Service:text", ExtractionColumn("Service", "text")),
        ("Paid on:date", ExtractionColumn("Paid on", "date")),
        ("Amount:number", ExtractionColumn("Amount", "number")),
        ("Auto:yes_no", ExtractionColumn("Auto", "yes_no")),
        ("Size: large:text", ExtractionColumn("Size: large", "text")),
        ("Event:one_of:a|b|c", ExtractionColumn("Event", "one_of", ("a", "b", "c"))),
        ("Event: x:one_of:a | b", ExtractionColumn("Event: x", "one_of", ("a", "b"))),
    ],
)
def test_target_parsing(spec: str, expected: ExtractionColumn) -> None:
    assert parse_target(spec) == expected


@pytest.mark.parametrize(
    "spec",
    ["Name", "Name:color", ":text", "Name:one_of", "Name:one_of:", "Name:one_of:only",
     "Name:text:a|b", "Name:number:EUR"],
)  # fmt: skip
def test_bad_targets_are_refused(spec: str) -> None:
    with pytest.raises(ValueError, match="--target"):
        parse_target(spec)


def fails(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as stop:
        run(argv, client=RowApi(choose))
    assert stop.value.code == 2
    return capsys.readouterr().err


def test_option_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "o"
    assert "At least one --target" in fails(["--eml-dir", str(FIXTURES), "--task", "q"], capsys)
    assert "Folder not found" in fails(args(out, eml=tmp_path / "nope"), capsys)
    assert "--limit" in fails(args(out, "--limit", "0"), capsys)
    assert "--max-chars" in fails(args(out, "--max-chars", "10"), capsys)
    assert "not valid" not in fails(
        [*args(out)[:4], "--target", "Event:one_of:a", "--out-dir", str(out)], capsys
    )  # message names the target
    assert "--task cannot be empty" in fails(
        ["--eml-dir", str(FIXTURES), "--task", " ", *TARGETS], capsys
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "No readable .eml files" in fails(args(out, eml=empty), capsys)
    assert not out.exists()


def test_a_missing_key_is_an_option_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(SystemExit):
        run(args(tmp_path / "o"))
    assert "OPENAI_API_KEY" in capsys.readouterr().err


# --- dry run ---------------------------------------------------------------------------


def test_dry_run_sends_nothing_and_prints_the_exact_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import openai

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("a client was constructed")

    monkeypatch.setattr(openai, "OpenAI", forbidden)
    monkeypatch.delenv("OPENAI_API_KEY")
    api = RowApi(choose)
    assert run(args(tmp_path / "o", "--dry-run"), client=api) == 0
    assert api.calls == [] and not (tmp_path / "o").exists()
    output = capsys.readouterr().out
    assert "First of 10 messages (06_streamly_ended_uk.eml); nothing is sent." in output
    assert (
        "Masking replaced in this message: card 0, me 0, phone 0; in all 10: card 3, me 1, phone 1"
        in output
    )
    printed = json.loads(output[output.index("{") :])
    task = ExtractionTask(
        QUESTION,
        (
            ExtractionColumn("Service", "text"),
            ExtractionColumn("Amount", "number"),
            ExtractionColumn("Paid on", "date"),
            ExtractionColumn("Event", "one_of", ("payment", "renewal notice", "ended")),
        ),
    )
    document = printed["input"].split("---\n", 1)[1].removesuffix("\n---")
    assert document.startswith(
        "Subject: Підписку Streamly завершено\n"
        "From: Streamly <hello@streamly.example.com>\n"
        "Date: 2026-10-30\n\n"
    )
    wanted = build_request(task, document, model="gpt-6-luna", reasoning_effort="low")
    assert printed == {**wanted, "timeout": 90.0}


def test_dry_run_shows_what_masking_does_on_the_mail_with_card_phone_and_address(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    only = tmp_path / "only"
    only.mkdir()
    (only / "10.eml").write_bytes((FIXTURES / "10_payrelay_card_phone_me.eml").read_bytes())
    assert run(args(tmp_path / "o", "--dry-run", eml=only), client=RowApi(choose)) == 0
    output = capsys.readouterr().out
    assert "Masking replaced in this message: card 3, me 1, phone 1" in output
    prompt = json.loads(output[output.index("{") :])["input"]
    for placeholder in ("Mastercard [card].", "Visa-[card]", "ending in [card]", "[phone]", "[me]"):
        assert placeholder in prompt
    for secret in ("0504", "6464", "1234", "555 010", "sam.example@example.org"):
        assert secret not in prompt
    # Names and postal addresses are not masked; the probe says so.
    assert "12 Sample Street" in prompt


def test_no_mask_sends_the_text_as_it_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    only = tmp_path / "only"
    only.mkdir()
    (only / "10.eml").write_bytes((FIXTURES / "10_payrelay_card_phone_me.eml").read_bytes())
    run(args(tmp_path / "o", "--dry-run", "--no-mask", eml=only), client=RowApi(choose))
    output = capsys.readouterr().out
    assert "card 0, me 0, phone 0" in output and "masking is off" in output
    prompt = json.loads(output[output.index("{") :])["input"]
    assert "0504" in prompt and "sam.example@example.org" in prompt


# --- a full run ------------------------------------------------------------------------


def test_full_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api = fake()
    out = tmp_path / "out"
    assert run(args(out), client=api) == 0
    console = capsys.readouterr().out
    assert len(api.calls) == 10
    assert sorted(p.name for p in out.iterdir()) == ["register.xlsx", "rows.json", "summary.json"]

    # what was sent: masked, with subject and sender, and the key nowhere
    inputs = "\n".join(call["input"] for call in api.calls)
    assert all(call["store"] is False and "tools" not in call for call in api.calls)
    for secret in ("0504", "6464", "sam.example@example.org", "555 010 0199"):
        assert secret not in inputs
    assert "[card]" in inputs and "[phone]" in inputs and "[me]" in inputs
    assert "http" not in inputs  # no link reaches the model
    assert "͏" not in inputs and "​" not in inputs

    rows = json.loads((out / "rows.json").read_text(encoding="utf-8"))
    assert [r["source"][:2] for r in rows] == [
        "06",
        "01",
        "03",
        "10",
        "07",
        "09",
        "02",
        "08",
        "04",
        "05",
    ]
    by_source = {r["source"][:2]: r for r in rows}
    assert {k: v["outcome"] for k, v in by_source.items()} == {
        "01": "FOUND", "02": "FOUND", "03": "FOUND", "04": "FOUND", "05": "FOUND",
        "06": "FOUND", "07": "PROVIDER_ERROR", "08": "NOT_APPLICABLE",
        "09": "NOT_APPLICABLE", "10": "FOUND",
    }  # fmt: skip

    german = by_source["01"]
    assert german["values"]["Amount"] == {
        "value": 10.0,
        "mark": "literal",
        "quote": "Gesamt: 10,00 EUR",
    }
    assert german["values"]["Paid on"]["value"] == "2026-11-01"
    assert german["values"]["Event"]["mark"] == "derived"
    assert german["received"] == "2026-11-01T06:12:44+01:00"
    assert german["sender_name"] == "Cloudbox Billing" and german["message_id"].startswith(
        "20261101"
    )
    assert german["subject"] == "Ihre Rechnung von Cloudbox – November 2026"
    assert by_source["04"]["dropped"] == [
        {"column": "Event", "reason": "invalid_value", "value": "trial ending",
         "quote": "ends on 20 November 2026"}
    ]  # fmt: skip
    assert by_source["05"]["dropped"][0]["reason"] == "number_not_in_quote"
    assert by_source["10"]["masking"] == {"card": 3, "phone": 1, "me": 1}
    assert by_source["07"]["values"] == {} and by_source["07"]["message"]
    assert by_source["10"]["truncated"] is False

    # rows.json holds facts and answers, never the message text or the recipient
    raw = (out / "rows.json").read_text(encoding="utf-8")
    for body_text in (
        "vielen Dank für Ihre Zahlung", "Sample Street", "sam.example@example.org",
        "Спасибо за оплату", "Open the app and start watching", "Copy this link",
    ):  # fmt: skip
        assert body_text not in raw
    assert KEY not in raw and "sk-" not in raw

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["configuration_id"] == "openai-text-extraction-p1-gpt-6-luna-low"
    assert summary["messages"] == {
        "read": 10, "skipped": 0, "skipped_files": [], "asked": 10, "stopped_early": None
    }  # fmt: skip
    assert summary["outcomes"] == {"FOUND": 7, "PROVIDER_ERROR": 1, "NOT_APPLICABLE": 2}
    assert summary["values_kept_by_mark"] == {"literal": 17, "derived": 5}
    assert summary["values_dropped_by_reason"] == {"invalid_value": 1, "number_not_in_quote": 1}
    assert summary["masking_counts"] == {"card": 3, "phone": 1, "me": 1}
    assert summary["usage"] == {"input_tokens": 9 * 700, "output_tokens": 9 * 90}
    assert summary["estimated_cost"] == round((9 * 700 * 0.10 + 9 * 90 * 0.50) / 1e6, 4)
    assert summary["truncated_messages"] == 0

    assert "Messages: 10 read, 0 skipped (not readable), 10 asked" in console
    assert "Outcomes: FOUND 7, NOT_APPLICABLE 2, PROVIDER_ERROR 1" in console
    assert "Values kept, by mark: derived 5, literal 17" in console
    assert "Values dropped, by reason: invalid_value 1, number_not_in_quote 1" in console
    assert "Masking: card 3, me 1, phone 1" in console
    assert "Estimated cost:" in console and "it was not compared with the bill" in console
    assert "Tokens in / out: 6300 / 810" in console
    assert KEY not in console


def test_the_workbook(tmp_path: Path) -> None:
    out = tmp_path / "out"
    run(args(out), client=fake())
    book = load_workbook(out / "register.xlsx")
    assert book.sheetnames == ["Register", "Other", "Checks"]

    register = book["Register"]
    header = [c.value for c in register[1]]
    assert header == ["Received", "From", "Subject", "Service", "Amount", "Paid on", "Event"]
    rows = list(register.iter_rows(min_row=2))
    assert len(rows) == 7
    first = rows[1]  # the German receipt (the Ukrainian notice is older)
    assert first[0].value == datetime(2026, 11, 1) and first[0].number_format == "yyyy-mm-dd"
    assert first[1].value == "Cloudbox Billing"
    assert first[2].value == "Ihre Rechnung von Cloudbox – November 2026"
    assert first[3].value == "Cloudbox Plus"
    assert first[4].value == 10 and isinstance(first[4].value, int | float)
    assert first[5].value == datetime(2026, 11, 1) and first[5].number_format == "yyyy-mm-dd"
    assert first[6].value == "payment"
    assert first[4].comment is not None
    assert first[4].comment.text == "literal\nGesamt: 10,00 EUR"
    assert first[6].comment is not None and first[6].comment.text.startswith("derived\n")
    assert first[3].comment is not None and first[3].comment.text.startswith("literal\n")
    # a value that was not kept leaves its cell empty and has no note
    price_free = [r for r in rows if r[2].value == "Your Streamly trial ends on 20 November"][0]
    assert (
        price_free[5].value is None
        and price_free[5].comment is None
        and price_free[6].value is None
    )

    other = list(book["Other"].iter_rows(values_only=True))
    assert other[0] == ("Received", "From", "Subject", "Outcome")
    assert [(r[1], r[3]) for r in other[1:]] == [
        ("HostHill", "PROVIDER_ERROR"),
        ("AppMarket", "NOT_APPLICABLE"),
        ("Streamly", "NOT_APPLICABLE"),
    ]
    assert other[2][0] == datetime(2026, 11, 11)

    checks: list[tuple[Any, ...]] = list(book["Checks"].iter_rows(values_only=True))
    assert checks[0] == ("Source file", "Column", "Value", "Mark or reason", "Quote")
    assert len(checks) - 1 == 22 + 2  # every kept and every dropped value
    line = [c for c in checks if c[0].startswith("01") and c[1] == "Amount"][0]
    assert line == ("01_cloudbox_rechnung_de.eml", "Amount", 10, "literal", "Gesamt: 10,00 EUR")
    dropped = [c for c in checks if c[3] in ("invalid_value", "number_not_in_quote")]
    assert sorted(c[3] for c in dropped) == ["invalid_value", "number_not_in_quote"]
    assert [c for c in checks if c[1] == "Paid on" and c[0].startswith("02")][0][2] == datetime(
        2026, 11, 12
    )


def message(
    subject: str = "Hello", name: str = "", address: str = "billing@example.com"
) -> MailMessage:
    return MailMessage(
        message_id="x", received=datetime(2026, 11, 1, 23, 30, tzinfo=UTC), sender_name=name,
        sender_address=address, recipient_address="me@example.org", subject=subject, text="Body",
        truncated=False, source="x.eml",
    )  # fmt: skip


def test_text_that_looks_like_a_formula_stays_text(tmp_path: Path) -> None:
    task = ExtractionTask("q", (ExtractionColumn("Service", "text"),))
    kept = KeptValue("Service", "value_1", "text", "=2+2", "derived", "+cmd|' /C calc'!A0")
    result = ExtractionResult(OUTCOME_FOUND, kept=(kept,))
    write_workbook(tmp_path / "w.xlsx", task, [(message(subject="=1+1", name="@sum"), result)])
    sheet = load_workbook(tmp_path / "w.xlsx")["Register"]
    for cell in (sheet["B2"], sheet["C2"], sheet["D2"]):
        assert cell.data_type == "s", cell.coordinate
    assert (sheet["B2"].value, sheet["C2"].value, sheet["D2"].value) == ("@sum", "=1+1", "=2+2")
    assert sheet["A2"].value == datetime(2026, 11, 1)


def test_text_with_control_characters_does_not_break_the_workbook(tmp_path: Path) -> None:
    task = ExtractionTask("q", (ExtractionColumn("Service", "text"),))
    kept = KeptValue("Service", "value_1", "text", "A\x01B", "derived", "quote\x02")
    result = ExtractionResult(OUTCOME_FOUND, kept=(kept,))
    write_workbook(tmp_path / "w.xlsx", task, [(message(subject="Sub\x0bject"), result)])
    sheet = load_workbook(tmp_path / "w.xlsx")["Register"]
    assert sheet["C2"].value == "Subject" and sheet["D2"].value == "AB"


def test_from_is_the_name_or_else_the_domain() -> None:
    assert from_text(message(name="Cloudbox Billing")) == "Cloudbox Billing"
    assert (
        from_text(message(name="", address="billing@cloudbox.example.com"))
        == "cloudbox.example.com"
    )
    assert from_text(message(name="", address="")) == ""


def test_the_document_has_subject_sender_and_date_then_the_body() -> None:
    assert message_document(message(subject="A  B", name="Cloudbox")) == (
        "Subject: A B\nFrom: Cloudbox <billing@example.com>\nDate: 2026-11-01\n\nBody"
    )
    undated = MailMessage("", None, "", "", "", "S", "Body", False, "u.eml")
    assert message_document(undated) == "Subject: S\nFrom: \n\nBody"


# --- failures and limits ---------------------------------------------------------------


def test_a_rejected_request_stops_the_run_and_the_report_says_where(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RowApi(lambda kwargs: StatusError(400, f"bad {KEY}"), api_key=KEY)
    out = tmp_path / "out"
    assert run(args(out), client=api) == 0
    assert len(api.calls) == 1
    console = capsys.readouterr().out
    assert "Stopped after 06_streamly_ended_uk.eml: REQUEST_REFUSED" in console
    assert "Run with --dry-run" in console
    assert "[key]" in console and KEY not in console  # the API's text is shown once, redacted
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["messages"]["asked"] == 1
    assert summary["messages"]["stopped_early"] == "REQUEST_REFUSED at message 1"
    rows = json.loads((out / "rows.json").read_text(encoding="utf-8"))
    assert [r["outcome"] for r in rows] == ["REQUEST_REFUSED"]


def test_a_refused_key_stops_too(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api = RowApi(lambda kwargs: StatusError(401), api_key=KEY)
    run(args(tmp_path / "o"), client=api)
    assert len(api.calls) == 1
    assert "Check the key" in capsys.readouterr().out


def test_other_failures_do_not_stop_the_run(tmp_path: Path) -> None:
    calls = iter([StatusError(500), TimeoutError("slow"), response("not json"), response(reply())])
    api = RowApi(lambda kwargs: next(calls, response(reply(applies=False))), api_key=KEY)
    out = tmp_path / "out"
    run(args(out), client=api)
    assert len(api.calls) == 10
    rows = json.loads((out / "rows.json").read_text(encoding="utf-8"))
    assert [r["outcome"] for r in rows[:4]] == [
        "PROVIDER_ERROR", "PROVIDER_ERROR", "INVALID_RESPONSE", "NOTHING_FOUND"
    ]  # fmt: skip
    sheet = load_workbook(out / "register.xlsx")["Other"]
    assert sheet.max_row == 11  # header and ten messages, none of them FOUND


def test_limit_takes_the_oldest_messages(tmp_path: Path) -> None:
    api = fake()
    out = tmp_path / "out"
    run(args(out, "--limit", "3"), client=api)
    assert len(api.calls) == 3
    rows = json.loads((out / "rows.json").read_text(encoding="utf-8"))
    assert [r["source"][:2] for r in rows] == ["06", "01", "03"]


def test_unreadable_files_are_counted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = tmp_path / "mails"
    folder.mkdir()
    (folder / "ok.eml").write_bytes((FIXTURES / "09_appmarket_promo_prices.eml").read_bytes())
    (folder / "broken.eml").write_bytes(b"")
    out = tmp_path / "out"
    run(args(out, eml=folder), client=fake())
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["messages"]["skipped"] == 1 and summary["messages"]["skipped_files"] == [
        "broken.eml"
    ]
    assert "Messages: 1 read, 1 skipped (not readable), 1 asked" in capsys.readouterr().out


def test_a_long_mail_is_cut_and_counted(tmp_path: Path) -> None:
    folder = tmp_path / "mails"
    folder.mkdir()
    body = "\n".join(f"Line {i} of a very long receipt text" for i in range(200))
    (folder / "long.eml").write_text(
        f"From: A <a@example.com>\nSubject: Long\nDate: Mon, 02 Nov 2026 10:00:00 +0000\n\n{body}\n"
    )
    api = fake()
    out = tmp_path / "out"
    run(args(out, "--max-chars", "500", eml=folder), client=api)
    assert len(api.calls[0]["input"]) < 900
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["truncated_messages"] == 1 and summary["max_chars"] == 500
    rows = json.loads((out / "rows.json").read_text(encoding="utf-8"))
    assert rows[0]["truncated"] is True


def test_the_default_output_folder_is_under_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    argv = ["--eml-dir", str(FIXTURES), "--task", QUESTION, *TARGETS, "--limit", "1"]
    run(argv, client=fake())
    [folder] = (tmp_path / "artifacts" / "mail-extraction").iterdir()
    assert len(folder.name) == 16 and folder.name.endswith("Z")
    assert sorted(p.name for p in folder.iterdir()) == [
        "register.xlsx",
        "rows.json",
        "summary.json",
    ]
    assert date.fromisoformat  # keep the import used


def test_the_fixture_folder_is_all_invented() -> None:
    readme = (FIXTURES / "README.md").read_text(encoding="utf-8")
    assert "invented" in readme.lower()
    for path in FIXTURES.glob("*.eml"):
        data = path.read_bytes().decode("utf-8", errors="replace")
        assert "example." in data


def test_the_openai_path_has_no_retry_and_no_attempts(tmp_path: Path) -> None:
    def lost(kwargs: dict[str, Any]) -> Any:
        if "Subject: Ihre Rechnung" in kwargs["input"]:
            return TimeoutError("the request timed out")
        return choose(kwargs)

    api = RowApi(lost, api_key=KEY)
    out = tmp_path / "o"
    assert run(args(out), client=api) == 0
    assert len(api.calls) == 10  # one request per mail, none repeated
    rows = json.loads((out / "rows.json").read_text(encoding="utf-8"))
    assert [r["outcome"] for r in rows].count("PROVIDER_ERROR") >= 1
    assert all("attempts" not in r for r in rows)
