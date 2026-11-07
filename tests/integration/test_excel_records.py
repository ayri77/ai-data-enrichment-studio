"""Test the excel records ingestion."""

from pathlib import Path

import pytest
from openpyxl import Workbook

from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    PlanStep,
    required_input_columns,
)
from ai_data_enrichment.ingestion.excel_records import read_input_batch, read_input_records
from ai_data_enrichment.ingestion.table_selection import TableSelection


def test_reads_rows_with_original_excel_row_numbers(tmp_path: Path) -> None:
    path = tmp_path / "cities.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None

    sheet.title = "Cities"
    sheet.append(["City", "Country"])
    sheet.append(["Berlin", "Germany"])
    sheet.append([None, None])
    sheet.append(["Hamburg", "Germany"])

    workbook.save(path)
    workbook.close()

    records = read_input_records(
        path,
        selection=TableSelection(
            sheet_name="Cities",
            range_ref="A1:B4",
        ),
        input_columns=("City", "Country"),
    )

    assert [record.record_id for record in records] == [
        "Cities!2",
        "Cities!4",
    ]

    assert [record.values for record in records] == [
        {"City": "Berlin", "Country": "Germany"},
        {"City": "Hamburg", "Country": "Germany"},
    ]


def test_reads_selected_input_columns(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Report"

    sheet["B6"] = "City"
    sheet["C6"] = "Country"
    sheet["D6"] = "Distance"

    sheet["B7"] = "Berlin"
    sheet["C7"] = "Germany"
    sheet["D7"] = "=100+50"

    # Row 8 intentionally empty.

    sheet["B9"] = "Hamburg"
    sheet["C9"] = "Germany"

    workbook.save(path)
    workbook.close()

    plan = EnrichmentPlan(
        version="1",
        steps=[
            PlanStep(
                id="geocode_city",
                capability="geocode",
                inputs={
                    "city": ColumnReference(
                        kind="column",
                        name="City",
                    ),
                    "country": ColumnReference(
                        kind="column",
                        name="Country",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    records = read_input_records(
        path,
        selection=TableSelection("Report", "B6:D10"),
        input_columns=required_input_columns(plan),
    )

    assert [record.record_id for record in records] == [
        "Report!7",
        "Report!9",
    ]

    assert [record.values for record in records] == [
        {"City": "Berlin", "Country": "Germany"},
        {"City": "Hamburg", "Country": "Germany"},
    ]


def test_reads_numeric_input_columns(tmp_path: Path) -> None:
    path = tmp_path / "mountains.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Mountains"

    sheet.append(["Mountain", "Elevation", "Rank"])
    sheet.append(["Everest", 8848.86, 1])

    workbook.save(path)
    workbook.close()

    records = read_input_records(
        path,
        selection=TableSelection("Mountains", "A1:C2"),
        input_columns=("Mountain", "Elevation", "Rank"),
    )

    assert len(records) == 1

    assert records[0].values == {
        "Mountain": "Everest",
        "Elevation": 8848.86,
        "Rank": 1.0,
    }

    assert isinstance(records[0].values["Rank"], float)


def test_rejects_input_exceeding_max_rows(tmp_path: Path) -> None:
    path = tmp_path / "cities.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Cities"

    sheet.append(["City"])
    sheet.append(["Berlin"])
    sheet.append([None])
    sheet.append(["Hamburg"])
    sheet.append(["Vienna"])

    workbook.save(path)
    workbook.close()

    with pytest.raises(ValueError, match="Input exceeds max_rows=2"):
        read_input_records(
            path,
            selection=TableSelection("Cities", "A1:A5"),
            input_columns=("City",),
            max_rows=2,
        )


def test_rejects_formula_in_required_input(tmp_path: Path) -> None:
    path = tmp_path / "formulas.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Data"

    sheet.append(["City", "Value"])
    sheet.append(["Berlin", "=100+50"])

    workbook.save(path)
    workbook.close()

    with pytest.raises(
        ValueError,
        match="Unsupported formula or error in B2",
    ):
        read_input_records(
            path,
            selection=TableSelection("Data", "A1:B2"),
            input_columns=("City", "Value"),
        )


def test_preserves_missing_input_values(tmp_path: Path) -> None:
    path = tmp_path / "missing_values.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Cities"

    sheet.append(["City", "Country"])
    sheet.append(["Berlin", None])
    sheet.append(["Hamburg", "Germany"])

    workbook.save(path)
    workbook.close()

    records = read_input_records(
        path,
        selection=TableSelection("Cities", "A1:B3"),
        input_columns=("City", "Country"),
    )

    assert [record.values for record in records] == [
        {"City": "Berlin", "Country": None},
        {"City": "Hamburg", "Country": "Germany"},
    ]


def test_input_batch_preserves_physical_row_mapping(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cities.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None

    sheet.title = "Cities!2026"

    sheet["B6"] = "City"
    sheet["C6"] = "Country"

    sheet["B7"] = "Berlin"
    sheet["C7"] = "Germany"

    # Row 8 is intentionally empty.

    sheet["B9"] = "Hamburg"
    sheet["C9"] = "Germany"

    workbook.save(path)
    workbook.close()

    batch = read_input_batch(
        path,
        selection=TableSelection(
            "Cities!2026",
            "B6:C9",
        ),
        input_columns=("City", "Country"),
    )

    assert [record.record_id for record in batch.records] == [
        "Cities!2026!7",
        "Cities!2026!9",
    ]

    assert dict(batch.row_by_record_id) == {
        "Cities!2026!7": 7,
        "Cities!2026!9": 9,
    }
