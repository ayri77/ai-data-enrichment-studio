"""Write explicitly selected cells to a new XLSX workbook."""

import os
from collections.abc import Collection, Iterable, Mapping
from math import isfinite
from pathlib import Path
from tempfile import mkstemp
from zipfile import ZipFile

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.utils.cell import coordinate_to_tuple
from openpyxl.worksheet.worksheet import Worksheet


def _checked_empty_target_cell(
    sheet: Worksheet,
    coordinate: str,
    *,
    may_hold_value: bool = False,
) -> Cell:
    """Resolve an Excel cell and reject unsafe write targets."""

    try:
        row, column = coordinate_to_tuple(coordinate)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid cell coordinate: {coordinate}") from exc

    if not 1 <= row <= 1_048_576:
        raise ValueError(f"Row out of range: {coordinate}")

    if not 1 <= column <= 16_384:
        raise ValueError(f"Column out of range: {coordinate}")

    cell = sheet.cell(row=row, column=column)

    if isinstance(cell, MergedCell):
        raise ValueError(f"Target cell belongs to a merged range: {coordinate}")

    if cell.value is not None and not may_hold_value:
        raise ValueError(f"Target cell is not empty: {coordinate}")

    return cell


def write_excel_cells(
    source_path: Path,
    output_path: Path,
    *,
    sheet_name: str,
    writes: Mapping[str, str | float],
    replace: Collection[str] = (),
) -> Path:
    """Write values to empty cells in a separate workbook.

    A cell named in ``replace`` may already hold a value, which the write then
    replaces. Every other target must be empty. The caller names such cells one
    by one, so nothing is overwritten that it did not decide to overwrite.
    """

    if source_path.suffix.lower() != ".xlsx":
        raise ValueError("Source must be an .xlsx file")

    if output_path.suffix.lower() != ".xlsx":
        raise ValueError("Output must be an .xlsx file")

    if source_path.resolve() == output_path.resolve():
        raise ValueError("Source and output paths must differ")

    if output_path.exists():
        raise FileExistsError(output_path)

    workbook = load_workbook(
        source_path,
        read_only=False,
        data_only=False,
    )

    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"Worksheet '{sheet_name}' not found")

        sheet = workbook[sheet_name]
        pending = []

        # Validate every proposed write before modifying cells.
        for coordinate, value in writes.items():
            cell = _checked_empty_target_cell(
                sheet, coordinate, may_hold_value=coordinate in replace
            )

            if not (isinstance(value, str) or type(value) is float):
                raise ValueError(f"Unsupported output value at {coordinate}")

            if type(value) is float and not isfinite(value):
                raise ValueError(f"Non-finite output value at {coordinate}")

            pending.append((cell, value))

        for cell, value in pending:
            cell.value = value

            # Keep text beginning with "=" as text, not a formula.
            if isinstance(value, str):
                cell.data_type = "s"

        # Create the temporary workbook in the destination directory.
        # This keeps temporary and final files on the same filesystem.
        fd, temporary_name = mkstemp(
            prefix=f".{output_path.stem}-",
            suffix=".xlsx",
            dir=output_path.parent,
        )
        os.close(fd)

        temporary_path = Path(temporary_name)

        try:
            workbook.save(temporary_path)

            # Verify that the generated XLSX archive is readable.
            with ZipFile(temporary_path) as archive:
                corrupt_member = archive.testzip()

                if corrupt_member is not None:
                    raise ValueError(f"Corrupt XLSX archive member: {corrupt_member}")

            # Verify that openpyxl can open the generated workbook.
            verified = load_workbook(
                temporary_path,
                read_only=True,
                data_only=False,
            )

            try:
                if sheet_name not in verified.sheetnames:
                    raise ValueError("Generated workbook lost the selected worksheet")
            finally:
                verified.close()

            # Publish without replacing an existing destination.
            os.link(temporary_path, output_path)

        finally:
            temporary_path.unlink(missing_ok=True)

    finally:
        workbook.close()

    return output_path


def validate_excel_targets(
    source_path: Path,
    *,
    sheet_name: str,
    coordinates: Iterable[str],
) -> None:
    """Validate target cells without modifying the source workbook."""

    workbook = load_workbook(
        source_path,
        read_only=False,
        data_only=False,
    )

    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"Worksheet '{sheet_name}' not found")

        sheet = workbook[sheet_name]

        for coordinate in coordinates:
            _checked_empty_target_cell(sheet, coordinate)

    finally:
        workbook.close()
