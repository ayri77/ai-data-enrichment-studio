"""Coordinate a validated Excel enrichment workflow."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from types import MappingProxyType
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    required_input_columns,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import JobExecutionResult
from ai_data_enrichment.execution.models import RowExecutionResult
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.ingestion.excel_records import read_input_batch
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.output.excel_output_mapping import (
    build_output_header_writes,
    build_output_value_writes,
)
from ai_data_enrichment.output.excel_writer import (
    validate_excel_targets,
    write_excel_cells,
)
from ai_data_enrichment.validation.plan_validator import (
    PlanValidationPolicy,
    PlanValidator,
)


def _recorded(value: object) -> str | float | bool:
    """An earlier cell value as it is recorded: a number, a flag, or its text.

    A formula is recorded as its text, a date as its ISO form.
    """
    if isinstance(value, bool | str):
        return value
    if isinstance(value, int | float):
        return float(value)
    return str(value.isoformat()) if isinstance(value, date | time) else str(value)


@dataclass(frozen=True, slots=True)
class ExcelEnrichmentResult:
    """Published workbook and its source-independent execution result."""

    output_path: Path
    job_result: JobExecutionResult
    # Output cells that already held a value and still hold it.
    preserved_cells: tuple[str, ...] = ()
    # Output cells whose earlier value was replaced, with that value, so that
    # nothing a person had in the table disappears without a record.
    replaced_cells: Mapping[str, str | float | bool] = MappingProxyType({})
    # Records left out of the run because every target cell already held a
    # value that is kept: for each, those cells with the value they hold. No
    # step ran for them and no source was asked.
    skipped_records: Mapping[str, Mapping[str, str | float | bool]] = MappingProxyType({})


class ExcelEnrichmentRunner:
    """Run a predefined enrichment plan against an explicit Excel table."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        executor: JobExecutor,
        max_rows: int = 1000,
        validation_policy: PlanValidationPolicy | None = None,
    ) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be positive")

        self._registry = registry
        self._executor = executor
        self._max_rows = max_rows
        self._validation_policy = validation_policy

    def run(
        self,
        *,
        source_path: Path,
        output_path: Path,
        selection: TableSelection,
        plan: EnrichmentPlan,
        output_columns: Mapping[str, str],
        dataset_id: str,
        previous_job_id: str | None = None,
        job_id: str | None = None,
        preserve_existing_outputs: bool = False,
        replace_existing_outputs: bool = False,
        on_record_done: Callable[[int, int], None] | None = None,
        on_row_result: Callable[[RowExecutionResult], None] | None = None,
    ) -> ExcelEnrichmentResult:
        """Validate, execute and publish one selected Excel table.

        ``on_record_done(done, total)`` and ``on_row_result(row)`` are passed to
        the executor unchanged.

        Output cells that already hold a value are an error by default. With
        ``preserve_existing_outputs`` they are tolerated and kept. Adding
        ``replace_existing_outputs`` writes a new value over an earlier one, but
        only where a new value was produced: a row whose lookup failed keeps
        what it had. The source workbook is never changed either way.

        A kept record whose every target cell already holds a value is not
        executed at all (``ExcelEnrichmentResult.skipped_records``); the
        executor, its counts and its request budgets never see it. A record
        with even one empty target cell is executed completely. Nothing is
        skipped when values may be replaced, or when existing values are not
        tolerated.
        """
        if replace_existing_outputs and not preserve_existing_outputs:
            raise ValueError("Replacing existing outputs requires tolerating them.")

        # Reject obvious destination conflicts before provider execution.
        if source_path.resolve() == output_path.resolve():
            raise ValueError("Source and output paths must differ")

        if output_path.exists():
            raise FileExistsError(output_path)

        # Profile exactly the table selected for execution.
        profile = profile_worksheet(
            path=source_path,
            sheet_name=selection.sheet_name,
            dataset_id=dataset_id,
            selection=selection,
        )

        min_col = selection.bounds[0]

        existing_headers = {
            get_column_letter(min_col + index): column.name
            for index, column in enumerate(profile.columns)
        }

        validation = PlanValidator(
            registry=self._registry,
            dataset_profile=profile,
            policy=self._validation_policy,
        ).validate(plan)

        if not validation.valid:
            details = "; ".join(f"{error.code}: {error.message}" for error in validation.errors)
            raise ValueError(f"Invalid enrichment plan: {details}")

        # Validate output column bindings before execution.
        header_writes = build_output_header_writes(
            plan,
            selection,
            output_columns=output_columns,
            existing_headers=existing_headers,
        )

        batch = read_input_batch(
            source_path,
            selection=selection,
            input_columns=required_input_columns(plan),
            max_rows=self._max_rows,
        )

        # Reserve every potential output cell before provider execution.
        # Check all accepted rows, including rows whose operations may fail.
        output_letters = tuple(
            get_column_letter(column_index_from_string(letter))
            for letter in output_columns.values()
        )

        target_coordinates = [
            *header_writes,
            *(
                f"{letter}{row_number}"
                for row_number in batch.row_by_record_id.values()
                for letter in output_letters
            ),
        ]

        # The API may fill empty destinations while retaining existing values.
        # Keep the default strict behavior for existing CLI/library callers.
        occupied: dict[str, str | float | bool] = {}
        if preserve_existing_outputs:
            workbook = load_workbook(source_path, read_only=False, data_only=False)
            try:
                sheet = workbook[selection.sheet_name]
                occupied = {
                    coordinate: _recorded(sheet[coordinate].value)
                    for coordinate in target_coordinates
                    if coordinate not in header_writes and sheet[coordinate].value is not None
                }
            finally:
                workbook.close()
        preserved = set(occupied)

        # A record needing nothing is left out before the executor is handed
        # anything. Replacing asks for a new value, so it skips nobody.
        skipped: dict[str, dict[str, str | float | bool]] = {}
        if preserve_existing_outputs and not replace_existing_outputs and output_letters:
            for record_id, row_number in batch.row_by_record_id.items():
                row_cells = [f"{letter}{row_number}" for letter in output_letters]
                if all(cell in occupied for cell in row_cells):
                    skipped[record_id] = {cell: occupied[cell] for cell in row_cells}
        records = [record for record in batch.records if record.record_id not in skipped]

        validate_excel_targets(
            source_path,
            sheet_name=selection.sheet_name,
            coordinates=(cell for cell in target_coordinates if cell not in preserved),
        )

        # Only passed when asked for: other executors need not know them.
        observers: dict[str, Any] = {}
        if on_record_done:
            observers["on_record_done"] = on_record_done
        if on_row_result:
            observers["on_row_result"] = on_row_result
        # Execute the validated DAG using the configured providers.
        job_result = self._executor.execute(
            plan,
            records,
            previous_job_id=previous_job_id,
            job_id=job_id,
            **observers,
        )

        # Map successful operation results to original Excel rows.
        value_writes = build_output_value_writes(
            plan,
            job_result,
            selection,
            row_by_record_id={
                record_id: row
                for record_id, row in batch.row_by_record_id.items()
                if record_id not in skipped
            },
            output_columns=output_columns,
            existing_headers=existing_headers,
        )

        # An earlier value gives way only to a new one, never to nothing.
        replaced = (
            {cell: occupied[cell] for cell in value_writes if cell in occupied}
            if replace_existing_outputs
            else {}
        )
        kept = preserved - set(replaced)

        # Publish only through the existing safe Excel writer.
        write_excel_cells(
            source_path,
            output_path,
            sheet_name=selection.sheet_name,
            writes={
                cell: value
                for cell, value in {**header_writes, **value_writes}.items()
                if cell not in kept
            },
            replace=tuple(replaced),
        )

        return ExcelEnrichmentResult(
            output_path=output_path,
            job_result=job_result,
            preserved_cells=tuple(sorted(kept)),
            replaced_cells=MappingProxyType(dict(sorted(replaced.items()))),
            skipped_records=MappingProxyType(skipped),
        )
