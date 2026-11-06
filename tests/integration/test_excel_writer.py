"""Integration tests for the excel_writer module."""

import pytest

from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from ai_data_enrichment.output.excel_writer import (
    write_excel_cells,
    validate_excel_targets,
)


def test_writes_new_workbook_and_preserves_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    output = tmp_path / "enriched.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Cities"

    sheet["A1"] = "City"
    sheet["B1"] = "Country"
    sheet["A2"] = "Berlin"
    sheet["B2"] = "Germany"

    # Existing formula outside the output cells.
    sheet["E2"] = "=1+2"

    notes = workbook.create_sheet("Notes")
    notes["A1"] = "Original notes"

    workbook.save(source)
    workbook.close()

    write_excel_cells(
        source,
        output,
        sheet_name="Cities",
        writes={
            "C1": "Distance",
            "C2": 104.5,
        },
    )

    assert output.exists()

    original = load_workbook(source, data_only=False)
    enriched = load_workbook(output, data_only=False)

    try:
        assert original["Cities"]["C1"].value is None
        assert original["Cities"]["C2"].value is None

        assert enriched["Cities"]["C1"].value == "Distance"
        assert enriched["Cities"]["C2"].value == 104.5

        assert enriched["Cities"]["E2"].value == "=1+2"
        assert enriched["Notes"]["A1"].value == "Original notes"

    finally:
        original.close()
        enriched.close()


import pytest


@pytest.mark.parametrize(
    "existing_value",
    ["Existing data", "=1+2"],
)
def test_rejects_overwriting_existing_cells(
    tmp_path: Path,
    existing_value: str,
) -> None:
    source = tmp_path / "source.xlsx"
    output = tmp_path / "enriched.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Data"

    sheet["B2"] = existing_value

    workbook.save(source)
    workbook.close()

    # First target is empty; second is occupied.
    writes = {
        "D2": 42.0,
        "B2": 99.0,
    }

    with pytest.raises(
        ValueError,
        match="Target cell is not empty: B2",
    ):
        write_excel_cells(
            source,
            output,
            sheet_name="Data",
            writes=writes,
        )

    # No partial output workbook must be created.
    assert not output.exists()

    # Original workbook remains unchanged.
    original = load_workbook(source, data_only=False)

    try:
        assert original["Data"]["B2"].value == existing_value
        assert original["Data"]["D2"].value is None
    finally:
        original.close()


def test_preserves_workbook_formatting(
    tmp_path: Path,
) -> None:
    source = tmp_path / "formatted.xlsx"
    output = tmp_path / "enriched.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Data"

    sheet["A1"] = "City"
    sheet["B1"] = "Country"
    sheet["A2"] = "Berlin"
    sheet["B2"] = "Germany"

    # Existing formatted cell.
    sheet["A1"].font = Font(
        bold=True,
        color="FF0055AA",
    )
    sheet["A1"].fill = PatternFill(
        fill_type="solid",
        fgColor="FFCCE5FF",
    )

    # Empty output cell with existing formatting.
    sheet["C2"].number_format = "0.00"

    # Worksheet-level formatting and structure.
    sheet.column_dimensions["B"].width = 24
    sheet.freeze_panes = "B2"

    sheet.merge_cells("F1:G1")
    sheet["F1"] = "Report notes"

    workbook.save(source)
    workbook.close()

    write_excel_cells(
        source,
        output,
        sheet_name="Data",
        writes={
            "C1": "Distance",
            "C2": 104.567,
        },
    )

    original = load_workbook(source)
    enriched = load_workbook(output)

    try:
        result = enriched["Data"]

        # Existing cell styling remains intact.
        assert result["A1"].font.bold is True
        assert result["A1"].font.color.rgb == "FF0055AA"
        assert result["A1"].fill.fgColor.rgb == "FFCCE5FF"

        # Output cell keeps its original number format.
        assert result["C2"].value == 104.567
        assert result["C2"].number_format == "0.00"

        # Worksheet structure remains intact.
        assert result.column_dimensions["B"].width == 24
        assert result.freeze_panes == "B2"
        assert "F1:G1" in result.merged_cells

        assert result["F1"].value == "Report notes"

        # Source workbook has not been modified.
        assert original["Data"]["C2"].value is None
        assert original["Data"]["C2"].number_format == "0.00"

    finally:
        original.close()
        enriched.close()


def test_failed_save_leaves_no_output_or_temp_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.xlsx"
    output = tmp_path / "enriched.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet["A1"] = "City"
    sheet["A2"] = "Berlin"

    workbook.save(source)
    workbook.close()

    # Simulate a disk/write failure after partial output.
    def failing_save(
        self: Workbook,
        filename: str | Path,
    ) -> None:
        Path(filename).write_bytes(b"incomplete workbook")
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Workbook, "save", failing_save)

    with pytest.raises(OSError, match="simulated disk failure"):
        write_excel_cells(
            source,
            output,
            sheet_name="Sheet",
            writes={"B2": 42.0},
        )

    assert not output.exists()

    # Only the original source should remain.
    assert {path.name for path in tmp_path.iterdir()} == {
        "source.xlsx",
    }


def test_publish_race_preserves_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.xlsx"
    output = tmp_path / "enriched.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None

    sheet["A1"] = "City"
    sheet["A2"] = "Berlin"

    workbook.save(source)
    workbook.close()

    competing_content = b"created by another process"

    def competing_link(
        _source: str | Path,
        destination: str | Path,
    ) -> None:
        # Simulate another process creating the destination
        # after the initial existence check.
        Path(destination).write_bytes(competing_content)
        raise FileExistsError("simulated concurrent publication")

    monkeypatch.setattr(
        "ai_data_enrichment.output.excel_writer.os.link",
        competing_link,
    )

    with pytest.raises(
        FileExistsError,
        match="simulated concurrent publication",
    ):
        write_excel_cells(
            source,
            output,
            sheet_name="Sheet",
            writes={"B2": 42.0},
        )

    # The competing process's file must remain untouched.
    assert output.read_bytes() == competing_content

    # Our temporary workbook must have been removed.
    assert {path.name for path in tmp_path.iterdir()} == {
        "source.xlsx",
        "enriched.xlsx",
    }


def test_preflight_rejects_occupied_cells_without_writing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None

    sheet.title = "Cities"
    sheet["A1"] = "City"
    sheet["A2"] = "Berlin"
    sheet["B2"] = "=1+2"

    workbook.save(source)
    workbook.close()

    original_bytes = source.read_bytes()

    # A free cell followed by an occupied formula cell.
    with pytest.raises(
        ValueError,
        match="Target cell is not empty: B2",
    ):
        validate_excel_targets(
            source,
            sheet_name="Cities",
            coordinates=("C1", "B2"),
        )

    # A valid set of targets passes without creating files.
    validate_excel_targets(
        source,
        sheet_name="Cities",
        coordinates=("C1", "C2"),
    )

    assert source.read_bytes() == original_bytes
    assert {path.name for path in tmp_path.iterdir()} == {
        "source.xlsx",
    }


def test_writer_rechecks_targets_after_preflight(
    tmp_path: Path,
) -> None:
    """Reject a target occupied after the initial preflight."""

    source = tmp_path / "source.xlsx"
    output = tmp_path / "enriched.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None

    sheet["A1"] = "mountain"
    sheet["A2"] = "Everest"

    workbook.save(source)
    workbook.close()

    # Initially both output cells are available.
    validate_excel_targets(
        source,
        sheet_name="Sheet",
        coordinates=("B1", "B2"),
    )

    # Simulate another process changing the source after preflight.
    workbook = load_workbook(source)
    sheet = workbook["Sheet"]
    sheet["B2"] = "Added by another process"

    workbook.save(source)
    workbook.close()

    # Writer must independently check the actual workbook state.
    with pytest.raises(
        ValueError,
        match="Target cell is not empty: B2",
    ):
        write_excel_cells(
            source,
            output,
            sheet_name="Sheet",
            writes={
                "B1": "height_m",
                "B2": 8848.86,
            },
        )

    # No partially published output.
    assert not output.exists()

    # The other process's changes must remain untouched.
    workbook = load_workbook(source)
    try:
        assert workbook["Sheet"]["B2"].value == (
            "Added by another process"
        )
    finally:
        workbook.close()
