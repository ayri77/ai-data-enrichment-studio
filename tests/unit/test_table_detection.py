"""Where the table is on a worksheet: a guess for the default selection."""

from ai_data_enrichment.ingestion.table_detection import detect_table

HEADER = ("City", "Country", "distance_km")


def region(rows):
    found = detect_table(rows)
    return found.range_ref if found else None


def test_a_table_in_the_corner_is_found_as_it_always_was() -> None:
    rows = [HEADER, ("Hamburg", "Germany", None), ("Lyon", "France", None)]
    assert region(rows) == "A1:C3"


def test_a_title_a_note_and_empty_columns_do_not_hide_the_table() -> None:
    rows = [
        (None, "REGIONAL DISTANCES"),
        (None, "Select the table below."),
        (),
        (None, None, *HEADER),
        (None, None, "Hamburg", "Germany", None),
        (None, None, "Paris", "France", 99999),
        (None, None, "Rome", "Italy", "=1+1"),
    ]
    assert region(rows) == "C4:E7"


def test_a_title_directly_above_the_header_is_not_the_header() -> None:
    rows = [("Report 2026",), HEADER, ("Hamburg", "Germany", 1.5)]
    assert region(rows) == "A2:C3"


def test_a_row_of_text_values_is_never_preferred_to_the_header_above_it() -> None:
    # Every data row here is unique text, just like a header.
    rows = [("From", "To"), ("Hamburg", "Bremen"), ("Berlin", "Leipzig"), ("Paris", "Lyon")]
    assert region(rows) == "A1:B4"


def test_the_table_ends_at_the_first_empty_row() -> None:
    rows = [HEADER, ("Hamburg", "Germany", None), (None, None, None), ("Source: survey", "x")]
    assert region(rows) == "A1:C2"


def test_numbers_formulas_and_repeated_names_are_not_headers() -> None:
    assert region([(2024, 2025), (1, 2)]) is None
    assert region([("=A1", "=B1"), (1, 2)]) is None
    assert region([("Name", "name"), ("a", "b")]) is None
    # One column is not enough to tell a header from a list.
    assert region([("Name",), ("a",), ("b",)]) is None
    # A header with nothing under it is not a table.
    assert region([HEADER]) is None
    assert region([HEADER, (None, None, None), ("a", "b", "c")]) is None
    assert region([]) is None


def test_of_two_tables_the_larger_wins_and_the_upper_one_on_a_tie() -> None:
    small = [("A", "B"), (1, 2)]
    large = [("K", "L", "M"), (1, 2, 3), (4, 5, 6)]
    assert region([*small, (), *large]) == "A4:C6"
    assert region([*small, (), *small]) == "A1:B2"
