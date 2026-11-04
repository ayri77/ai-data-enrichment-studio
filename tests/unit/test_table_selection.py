"""Test the TableSelection class."""

import pytest

from ai_data_enrichment.ingestion.table_selection import TableSelection


@pytest.mark.parametrize(
    ("range_ref", "expected_header", "expected_first_data"),
    [
        ("A1:C10", 1, 2),
        ("B6:D20", 6, 7),
        ("A1:C1", 1, 2),
    ],
)
def test_table_selection_coordinates(
    range_ref: str,
    expected_header: int,
    expected_first_data: int,
) -> None:
    selection = TableSelection(
        sheet_name="Report",
        range_ref=range_ref,
    )

    assert selection.header_row == expected_header
    assert selection.first_data_row == expected_first_data


@pytest.mark.parametrize(
    "invalid_range",
    [
        "D20:B6",
        "A0:B10",
        "A:C",
        "XFE1:XFE10",
        "OtherSheet!A1:B10",
    ],
)
def test_rejects_invalid_ranges(invalid_range: str) -> None:
    with pytest.raises(ValueError):
        TableSelection(
            sheet_name="Report",
            range_ref=invalid_range,
        )
