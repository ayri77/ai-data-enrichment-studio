"""The registry, conditions and matching of the rows core."""

from datetime import UTC, date, datetime

import pytest

from ai_data_enrichment.rows.conditions import Condition, check_conditions, passes
from ai_data_enrichment.rows.errors import RowsError
from ai_data_enrichment.rows.mail_files import MAIL_FILES
from ai_data_enrichment.rows.matching import key_part, propose_key, propose_mapping
from ai_data_enrichment.rows.sources import (
    ROW_SOURCES,
    Candidate,
    RowSource,
    SourceField,
    source_for,
)

NOW = datetime(2026, 11, 10, 12, 0)


def candidate(received: datetime | None, sender: str | None = "Shop") -> Candidate:
    return Candidate({"received": received, "sender": sender, "subject": "Hello"})


def test_registry_finds_mail_files_and_refuses_an_unknown_id() -> None:
    assert source_for("mail_files") is MAIL_FILES
    assert MAIL_FILES in ROW_SOURCES
    with pytest.raises(RowsError) as error:
        source_for("nope")
    assert error.value.code == "rows_unknown_source"


def test_a_source_checks_its_own_description() -> None:
    field = SourceField("a", "text", "A", ("a",))

    def make(fields: tuple[SourceField, ...], key: tuple[str, ...]) -> RowSource:
        return RowSource("x", "X", "files", (".x",), fields, key, "given", True)

    with pytest.raises(ValueError):
        make((field, field), ("a",))
    with pytest.raises(ValueError):
        make((field,), ("b",))
    assert make((field,), ("a",)).default_key == ("a",)


@pytest.mark.parametrize(
    ("condition", "received", "expected"),
    [
        (Condition("received", "within_last_days", 7), datetime(2026, 11, 4), True),
        (Condition("received", "within_last_days", 7), datetime(2026, 11, 2), False),
        (
            Condition("received", "on_or_after", date(2026, 11, 5)),
            datetime(2026, 11, 5, 0, 0),
            True,
        ),
        (
            Condition("received", "on_or_after", date(2026, 11, 5)),
            datetime(2026, 11, 4, 23, 59),
            False,
        ),
        (
            Condition("received", "on_or_before", date(2026, 11, 5)),
            datetime(2026, 11, 5, 23, 59),
            True,
        ),
        (Condition("received", "on_or_before", date(2026, 11, 5)), datetime(2026, 11, 6), False),
    ],
)
def test_moment_conditions(condition: Condition, received: datetime, expected: bool) -> None:
    check_conditions(MAIL_FILES, [condition])
    assert passes(candidate(received), [condition], now=NOW) is expected


def test_contains_ignores_case_and_a_missing_value_does_not_pass() -> None:
    condition = Condition("sender", "contains", "SHOP")
    check_conditions(MAIL_FILES, [condition])
    assert passes(candidate(NOW, "My shop Ltd"), [condition], now=NOW)
    assert not passes(candidate(NOW, "Bank"), [condition], now=NOW)
    assert not passes(candidate(NOW, None), [condition], now=NOW)
    dated = Condition("received", "on_or_after", date(2026, 1, 1))
    assert not passes(candidate(None), [dated], now=NOW)
    assert passes(candidate(NOW), [], now=NOW)


def test_all_conditions_have_to_hold() -> None:
    both = [Condition("sender", "contains", "shop"), Condition("received", "within_last_days", 3)]
    assert passes(candidate(datetime(2026, 11, 9)), both, now=NOW)
    assert not passes(candidate(datetime(2026, 11, 1)), both, now=NOW)


@pytest.mark.parametrize(
    "condition",
    [
        Condition("nothing", "contains", "x"),
        Condition("received", "contains", "x"),
        Condition("sender", "within_last_days", 3),
        Condition("received", "within_last_days", 0),
        Condition("received", "within_last_days", True),
        Condition("received", "within_last_days", "3"),
        Condition("received", "on_or_after", "2026-01-01"),
        Condition("received", "on_or_before", datetime(2026, 1, 1)),
        Condition("sender", "contains", ""),
        Condition("sender", "contains", "  "),
        Condition("sender", "contains", 5),
        Condition("sender", "starts_with", "x"),
    ],
)
def test_bad_conditions_are_refused(condition: Condition) -> None:
    with pytest.raises(RowsError) as error:
        check_conditions(MAIL_FILES, [condition])
    assert error.value.code == "rows_bad_condition"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (["Received", "From", "Subject", "Text"], {"received": 0, "sender": 1, "subject": 2}),
        ([" Datum ", "Absender", "BETREFF"], {"received": 0, "sender": 1, "subject": 2}),
        (["Дата", "Відправник", "Тема"], {"received": 0, "sender": 1, "subject": 2}),
        (["De  Bar", "from"], {"sender": 1}),
    ],
)
def test_propose_mapping_reads_english_german_and_ukrainian_headers(
    headers: list[str], expected: dict[str, int]
) -> None:
    assert propose_mapping(MAIL_FILES, headers) == expected


def test_an_ambiguous_field_is_left_out_and_a_column_is_used_once() -> None:
    assert propose_mapping(MAIL_FILES, ["Date", "Received", "From"]) == {"sender": 2}
    assert propose_mapping(MAIL_FILES, ["Date", "From", "From", "Subject"]) == {
        "received": 0,
        "subject": 3,
    }
    field = SourceField("a", "text", "A", ("same",))
    other = SourceField("b", "text", "B", ("same",))
    source = RowSource("x", "X", "files", (), (field, other), ("a",), "given", True)
    assert propose_mapping(source, ["Same"]) == {"a": 0}


def test_propose_key() -> None:
    assert propose_key(MAIL_FILES, {"received", "sender", "subject"}) == ("received", "sender")
    assert propose_key(MAIL_FILES, {"received", "subject"}) == ()


def test_key_parts() -> None:
    assert key_part("moment", datetime(2026, 1, 2, 3, 4, 59, 5)) == datetime(2026, 1, 2, 3, 4)
    aware = datetime(2026, 1, 2, 4, 4, 30, tzinfo=UTC)
    assert key_part("moment", aware) == datetime(2026, 1, 2, 4, 4)
    assert key_part("moment", "2026-01-02") is None
    assert key_part("moment", date(2026, 1, 2)) is None
    assert key_part("moment", 46000) is None
    assert key_part("text", "  Ａcme   SHOP ") == "acme shop"
    assert key_part("text", "   ") is None
    assert key_part("text", None) is None
    assert key_part("text", 12) == "12"
