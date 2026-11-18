"""The strict reply reader: every drop reason, every mark, every failure."""

import json
from typing import Any

import pytest
from tests.support.text_extraction import (
    TEXT,
    answer,
    entry,
    good_answer,
    mixed_task,
    response,
)

from ai_data_enrichment.providers.text_extraction import ExtractionColumn, ExtractionTask
from ai_data_enrichment.providers.text_extraction_reply import (
    ExtractionResult,
    date_parts,
    number_in_quote,
    numbers_in,
    parse_reply,
)


def read(reply: Any, text: str = TEXT, task: ExtractionTask | None = None) -> ExtractionResult:
    return parse_reply(response(reply), task or mixed_task(), text)


def one(column: ExtractionColumn, value: Any, quote: str | None, text: str = TEXT):  # noqa: ANN201
    task = ExtractionTask("q", (column,))
    reply = {"applies": True, "values": {"value_1": entry(value, quote)}}
    return parse_reply(response(reply), task, text)


# --- outcomes --------------------------------------------------------------------------


def test_everything_kept() -> None:
    result = read(good_answer())
    assert result.outcome == "FOUND" and not result.failed and result.dropped == ()
    kept = {v.column: v for v in result.kept}
    assert kept["Service"].mark == "literal" and kept["Service"].value == "Cloudbox Plus"
    assert kept["Amount"].mark == "literal" and kept["Amount"].value == 9.99
    assert kept["Paid on"].mark == "literal" and kept["Paid on"].value == "2026-11-03"
    assert kept["Auto renews"].mark == "derived" and kept["Auto renews"].value == "Yes"
    assert kept["Event"].mark == "derived"
    assert kept["Amount"].quote == "Total: 9,99 EUR" and kept["Amount"].slot == "value_2"
    assert (result.input_tokens, result.output_tokens) == (700, 90)


def test_not_applicable_has_no_values_even_if_the_model_gave_some() -> None:
    result = read({**good_answer(), "applies": False})
    assert result.outcome == "NOT_APPLICABLE" and result.kept == () and result.dropped == ()
    assert result.input_tokens == 700


def test_applies_but_nothing_said_is_nothing_found() -> None:
    result = read(answer())
    assert result.outcome == "NOTHING_FOUND" and result.kept == () and result.dropped == ()


def test_applies_and_everything_dropped_is_nothing_found() -> None:
    result = read(answer(value_1=entry("Cloudbox", "not in the text")))
    assert result.outcome == "NOTHING_FOUND" and len(result.dropped) == 1


def test_a_null_value_is_neither_kept_nor_dropped() -> None:
    result = read(answer(value_1=entry(None, "Cloudbox Plus, monthly.")))
    assert result.kept == () and result.dropped == ()


# --- the quote -------------------------------------------------------------------------


def test_no_quote_is_dropped() -> None:
    for quote in (None, "", "   "):
        result = one(ExtractionColumn("S", "text"), "Cloudbox Plus", quote)
        assert [d.reason for d in result.dropped] == ["quote_not_in_text"], quote


def test_a_quote_not_in_the_text_is_dropped_whatever_the_kind() -> None:
    cases = [
        (ExtractionColumn("S", "text"), "Cloudbox"),
        (ExtractionColumn("A", "number"), 9.99),
        (ExtractionColumn("D", "date"), "2026-11-03"),
        (ExtractionColumn("Y", "yes_no"), "Yes"),
        (ExtractionColumn("E", "one_of", ("a", "b")), "a"),
    ]
    for column, value in cases:
        result = one(column, value, "Total: 19,99 EUR")
        assert [d.reason for d in result.dropped] == ["quote_not_in_text"], column.kind
        assert result.dropped[0].value == value and result.dropped[0].quote == "Total: 19,99 EUR"


def test_the_quote_is_compared_after_collapsing_whitespace_and_is_case_sensitive() -> None:
    text = "Total:\n   9,99    EUR\n\nPaid on 3 Nov 2026"
    ok = one(ExtractionColumn("A", "number"), 9.99, "Total: 9,99 EUR", text)
    assert ok.outcome == "FOUND" and ok.kept[0].quote == "Total: 9,99 EUR"
    ok = one(ExtractionColumn("A", "number"), 9.99, "Total:\n9,99\tEUR", text)
    assert ok.outcome == "FOUND"
    bad = one(ExtractionColumn("A", "number"), 9.99, "total: 9,99 EUR", text)
    assert bad.dropped[0].reason == "quote_not_in_text"


def test_a_long_quote_is_cut_in_the_result_only_after_the_checks() -> None:
    text = "Total 9,99 EUR " + "filler " * 100
    quote = text.strip()
    result = one(ExtractionColumn("A", "number"), 9.99, quote, text)
    assert result.outcome == "FOUND" and len(result.kept[0].quote) == 300
    assert result.kept[0].quote.endswith("…")


# --- numbers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quote", "value"),
    [
        ("Total: 9,99 EUR", 9.99),
        ("Total: 9.99 EUR", 9.99),
        ("Gesamt 1.234,56 EUR", 1234.56),
        ("Total 1,234.56 USD", 1234.56),
        ("Стоимость: 909,99 ₽ в месяц", 909.99),
        ("$29.99", 29.99),
        ("€ 9,99", 9.99),
        ("9,99€", 9.99),
        ("EUR 10", 10.0),
        ("10,00 EUR", 10),
        ("1 234,56 EUR", 1234.56),
        ("1 234,56 EUR", 1234.56),
        ("CHF 1'234.50", 1234.5),
        ("12.000 EUR", 12000),
        ("12.000 EUR", 12.0),
        ("1.234.567 EUR", 1234567),
        ("Refund -9,99 EUR", -9.99),
        ("Refund −9.99 EUR", -9.99),
        ("9,994 EUR", 9.994),  # within the tolerance of 0.005
    ],
)
def test_numbers_that_are_in_the_quote(quote: str, value: float) -> None:
    assert number_in_quote(value, quote), numbers_in(quote)


@pytest.mark.parametrize(
    ("quote", "value"),
    [
        ("Total: 9,99 EUR", 10.99),
        ("Total: 9,99 EUR", 99.9),
        ("Total: 9,99 EUR", 999),
        ("Total: 19,99 EUR", 9.99),  # a part of a number is not the number
        ("Total: 9,99 EUR", -9.99),  # the sign is part of the number
        ("Paid 2026-11-03", -11),
        ("9,99", 9.98),
        ("1.2.3", 1.2),
        ("No digits here", 1),
    ],
)
def test_numbers_that_are_not_in_the_quote(quote: str, value: float) -> None:
    assert not number_in_quote(value, quote), numbers_in(quote)


def test_a_number_not_in_its_quote_is_dropped() -> None:
    result = one(ExtractionColumn("A", "number"), 12.5, "Total: 9,99 EUR")
    assert result.outcome == "NOTHING_FOUND"
    assert [(d.reason, d.value) for d in result.dropped] == [("number_not_in_quote", 12.5)]


def test_a_kept_number_is_literal() -> None:
    result = one(ExtractionColumn("A", "number"), 9.99, "Total: 9,99 EUR")
    assert (result.kept[0].mark, result.kept[0].value) == ("literal", 9.99)


# --- dates -----------------------------------------------------------------------------

DATE_TEXT = (
    "Paid on 3 Nov 2026. 2026-11-03. 03.11.2026. 11/03/2026. Nov 3, 2026. 3 November 2026. "
    "3. November 2026. 12 ноября 2026. 12 нояб. 2026. 30 жовтня 2026. 1. Mär 2027. "
    "Next: 18.11. Year 2027. The 5th. Order 7. 11.11.2026. Tuesday 11 Nov."
)


@pytest.mark.parametrize(
    ("quote", "value", "mark"),
    [
        ("2026-11-03", "2026-11-03", "literal"),
        ("03.11.2026", "2026-11-03", "literal"),
        ("11/03/2026", "2026-11-03", "literal"),
        ("3 Nov 2026", "2026-11-03", "literal"),
        ("Nov 3, 2026", "2026-11-03", "literal"),
        ("3 November 2026", "2026-11-03", "literal"),
        ("3. November 2026", "2026-11-03", "literal"),
        ("12 ноября 2026", "2026-11-12", "literal"),
        ("12 нояб. 2026", "2026-11-12", "literal"),
        ("30 жовтня 2026", "2026-10-30", "literal"),
        ("1. Mär 2027", "2027-03-01", "literal"),
        ("11.11.2026", "2026-11-11", "literal"),
        ("Tuesday 11 Nov", "2026-11-11", "derived"),  # no year
        ("Next: 18.11.", "2026-11-18", "derived"),  # no year
        ("Year 2027", "2027-05-09", "derived"),  # only the year
        ("3 Nov", "2026-11-03", "derived"),
        ("Renews next month, 3rd", "2026-12-03", "derived"),  # only the day
    ],
)
def test_date_marks(quote: str, value: str, mark: str) -> None:
    text = DATE_TEXT + " Renews next month, 3rd"
    result = one(ExtractionColumn("D", "date"), value, quote, text)
    assert [v.mark for v in result.kept] == [mark], result.dropped


def test_a_date_with_neither_day_nor_year_in_the_quote_is_dropped() -> None:
    result = one(
        ExtractionColumn("D", "date"), "2026-11-03", "Renews on Tuesday", "Renews on Tuesday"
    )
    assert [d.reason for d in result.dropped] == ["date_not_in_quote"]
    result = one(
        ExtractionColumn("D", "date"), "2026-11-03", "Paid in November", "Paid in November"
    )
    assert [d.reason for d in result.dropped] == ["date_not_in_quote"]


def test_day_and_month_need_two_tokens_when_they_are_equal_digits() -> None:
    # 2026-11-11 from "11 2026": the year and one 11 are there, a second 11 is not.
    assert date_parts("11 2026", 11, 11, 2026) == (True, False, True)
    assert date_parts("11.11.2026", 11, 11, 2026) == (True, True, True)
    assert date_parts("11 Nov 2026", 11, 11, 2026) == (True, True, True)
    assert date_parts("2026-11-18", 18, 11, 2026) == (True, True, True)
    assert date_parts("20261118", 18, 11, 2026) == (False, False, False)


@pytest.mark.parametrize(
    "value", ["2026-13-01", "2026-02-30", "26-11-03", "2026-1-3", "3 Nov 2026", "20261103", ""]
)
def test_an_invalid_date_string_is_dropped(value: str) -> None:
    result = one(ExtractionColumn("D", "date"), value, "Paid on 3 Nov 2026", DATE_TEXT)
    assert [d.reason for d in result.dropped] == ["invalid_value"], value


def test_month_names_in_four_languages() -> None:
    cases = {
        ("3 January 2026", 1),
        ("3 Jänner 2026", 1),
        ("3 января 2026", 1),
        ("3 січня 2026", 1),
        ("3 Feb 2026", 2),
        ("3 Februar 2026", 2),
        ("3 февраля 2026", 2),
        ("3 лютого 2026", 2),
        ("3 März 2026", 3),
        ("3 marta 2026".replace("marta", "марта"), 3),
        ("3 березня 2026", 3),
        ("3 Sept 2026", 9),
        ("3 сентября 2026", 9),
        ("3 вересня 2026", 9),
        ("3 Okt 2026", 10),
        ("3 октября 2026", 10),
        ("3 жовтня 2026", 10),
        ("3 Dezember 2026", 12),
        ("3 Dez 2026", 12),
        ("3 декабря 2026", 12),
        ("3 грудня 2026", 12),
        ("3 May 2026", 5),
        ("3 Mai 2026", 5),
        ("3 мая 2026", 5),
        ("3 травня 2026", 5),
    }
    for quote, month in cases:
        assert date_parts(quote, 3, month, 2026) == (True, True, True), quote
        other = 1 if month != 1 else 2
        assert date_parts(quote, 3, other, 2026)[1] is False, quote


# --- text, yes_no, one_of ---------------------------------------------------------------


def test_text_literal_or_derived() -> None:
    column = ExtractionColumn("S", "text")
    text = "Plan: Cloudbox  Plus (2 TB)\nTotal 9,99 EUR"
    result = one(column, "cloudbox plus", "Plan: Cloudbox Plus (2 TB)", text)
    assert [(v.mark, v.value) for v in result.kept] == [("literal", "cloudbox plus")]
    result = one(column, "Cloudbox", "Plan: Cloudbox Plus (2 TB)", text)
    assert result.kept[0].mark == "literal"  # contained in the quote
    result = one(column, "Cloud storage", "Plan: Cloudbox Plus (2 TB)", text)
    assert result.kept[0].mark == "derived"
    result = one(column, "  Cloudbox\n Plus ", "Plan: Cloudbox Plus (2 TB)", text)
    assert (result.kept[0].mark, result.kept[0].value) == ("literal", "Cloudbox Plus")


def test_blank_or_huge_text_is_invalid() -> None:
    column = ExtractionColumn("S", "text")
    assert one(column, "   ", "Total: 9,99 EUR").dropped[0].reason == "invalid_value"
    long = "x" * 600
    assert one(column, long, "Total: 9,99 EUR").dropped[0].reason == "invalid_value"


def test_yes_no_and_one_of_are_always_derived() -> None:
    yes = one(ExtractionColumn("Y", "yes_no"), "Yes", "Total: 9,99 EUR")
    assert (yes.kept[0].mark, yes.kept[0].value) == ("derived", "Yes")
    choice = ExtractionColumn("E", "one_of", ("payment", "ended"))
    result = one(choice, "ended", "Total: 9,99 EUR")
    assert (result.kept[0].mark, result.kept[0].value) == ("derived", "ended")


def test_a_value_outside_the_allowed_ones_is_dropped_as_invalid() -> None:
    assert one(ExtractionColumn("Y", "yes_no"), "yes", "Total: 9,99 EUR").dropped[0].reason == (
        "invalid_value"
    )
    choice = ExtractionColumn("E", "one_of", ("payment", "ended"))
    assert one(choice, "refund", "Total: 9,99 EUR").dropped[0].reason == "invalid_value"
    assert one(choice, "Payment", "Total: 9,99 EUR").dropped[0].reason == "invalid_value"


def test_slots_are_judged_independently() -> None:
    result = read(
        answer(
            value_1=entry("Cloudbox Plus", "Cloudbox Plus, monthly."),
            value_2=entry(12.0, "Total: 9,99 EUR"),
            value_3=entry("2026-11-03", "made up"),
            value_5=entry("payment", "Thank you."),
        )
    )
    assert result.outcome == "FOUND"
    assert [(v.column, v.mark) for v in result.kept] == [
        ("Service", "literal"),
        ("Event", "derived"),
    ]
    assert [(d.column, d.reason) for d in result.dropped] == [
        ("Amount", "number_not_in_quote"),
        ("Paid on", "quote_not_in_text"),
    ]


# --- failures --------------------------------------------------------------------------


def test_a_refusal() -> None:
    result = parse_reply(response("", refusal=True), mixed_task(), TEXT)
    assert result.outcome == "INVALID_RESPONSE" and "refused" in result.message
    assert result.failed and result.input_tokens == 700


def slot_reply(first: Any) -> dict[str, Any]:
    """A reply whose first slot is whatever is given, right or wrong."""
    return {"applies": True, "values": {**answer()["values"], "value_1": first}}


def broken_replies() -> list[tuple[str, Any]]:
    good = good_answer()
    return [
        ("no text", ""),
        ("not JSON", "this is not json"),
        ("not JSON", '{"applies": NaN, "values": {}}'),
        ("keys", {"applies": True}),
        ("keys", {**good, "extra": 1}),
        ("applies", {**good, "applies": "yes"}),
        ("applies", {**good, "applies": 1}),
        ("slots", {"applies": True, "values": {"value_1": entry()}}),
        ("slots", {"applies": True, "values": [entry()] * 5}),
        ("slots", {"applies": True, "values": {**good["values"], "value_6": entry()}}),
        ("object", slot_reply("Cloudbox")),
        ("object", slot_reply({"value": None})),
        ("object", slot_reply({**entry(), "extra": 1})),
        ("quote", slot_reply({"value": "x", "quote": 5})),
        ("type", answer(value_1=entry(5, "q"))),
        ("type", answer(value_2=entry("9,99", "q"))),
        ("type", answer(value_2=entry(True, "q"))),
        ("type", answer(value_2=entry(float("inf"), "q"))),
        ("type", answer(value_3=entry(20261103, "q"))),
        ("type", answer(value_4=entry(True, "q"))),
        ("list", []),
    ]


@pytest.mark.parametrize(
    ("name", "reply"), broken_replies(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_a_reply_of_the_wrong_shape_fails_the_whole_text(name: str, reply: Any) -> None:
    text = reply if isinstance(reply, str) else json.dumps(reply, allow_nan=True)
    result = parse_reply(response(text), mixed_task(), TEXT)
    assert result.outcome == "INVALID_RESPONSE", name
    assert result.kept == () and result.dropped == ()
    assert result.message.startswith("The reply does not have the requested shape")


def test_a_failure_message_never_carries_the_reply() -> None:
    result = parse_reply(response('{"applies": "SECRET-TEXT"}'), mixed_task(), TEXT)
    assert "SECRET-TEXT" not in result.message
