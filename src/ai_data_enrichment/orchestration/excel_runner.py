"""Coordinate a validated Excel enrichment workflow."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    required_input_columns,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import JobExecutionResult
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


@dataclass(frozen=True, slots=True)
class ExcelEnrichmentResult:
    """Published workbook and its source-independent execution result."""

    output_path: Path
    job_result: JobExecutionResult


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
    ) -> ExcelEnrichmentResult:
        """Validate, execute and publish one selected Excel table."""

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

        validate_excel_targets(
            source_path,
            sheet_name=selection.sheet_name,
            coordinates=target_coordinates,
        )

        # Execute the validated DAG using the configured providers.
        job_result = self._executor.execute(
            plan,
            batch.records,
            previous_job_id=previous_job_id,
        )

        # Map successful operation results to original Excel rows.
        value_writes = build_output_value_writes(
            plan,
            job_result,
            selection,
            row_by_record_id=batch.row_by_record_id,
            output_columns=output_columns,
        )

        # Publish only through the existing safe Excel writer.
        write_excel_cells(
            source_path,
            output_path,
            sheet_name=selection.sheet_name,
            writes={**header_writes, **value_writes},
        )

        return ExcelEnrichmentResult(
            output_path=output_path,
            job_result=job_result,
        )
