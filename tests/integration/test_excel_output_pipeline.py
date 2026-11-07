"""Integration test for Excel input-to-output mapping."""

from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
)
from ai_data_enrichment.execution.job_models import (
    JobExecutionMetrics,
    JobExecutionResult,
    RecordExecution,
)
from ai_data_enrichment.execution.models import (
    OperationResult,
    Quantity,
    RowExecutionResult,
    StepExecutionResult,
)
from ai_data_enrichment.ingestion.excel_records import read_input_batch
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.output.excel_output_mapping import (
    build_output_header_writes,
    build_output_value_writes,
)
from ai_data_enrichment.output.excel_writer import write_excel_cells


def test_excel_input_to_output_pipeline(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    output = tmp_path / "enriched.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Cities!2026"

    sheet["B6"] = "City"
    sheet["C6"] = "Country"
    sheet["D6"] = "Calculation"
    sheet["B6"].font = Font(bold=True)

    sheet["B7"] = "Berlin"
    sheet["C7"] = "Germany"
    sheet["D7"] = "=1+2"

    # Row 8 intentionally empty.
    sheet["B9"] = "Hamburg"
    sheet["C9"] = "Germany"

    # Row 10 intentionally empty.
    sheet["B11"] = "Vienna"
    sheet["C11"] = "Austria"

    # An unrelated region between the source table and output.
    sheet["F6"] = "Separate data"
    sheet["F7"] = "Preserve me"

    notes = workbook.create_sheet("Notes")
    notes["A1"] = "Original notes"

    workbook.save(source)
    workbook.close()

    original_bytes = source.read_bytes()

    selection = TableSelection(
        sheet_name="Cities!2026",
        range_ref="B6:D11",
    )

    batch = read_input_batch(
        source,
        selection=selection,
        input_columns=("City", "Country"),
    )

    assert [record.record_id for record in batch.records] == [
        "Cities!2026!7",
        "Cities!2026!9",
        "Cities!2026!11",
    ]

    plan = EnrichmentPlan(
        version="1",
        steps=[
            PlanStep(
                id="lookup",
                capability="distance_lookup",
                inputs={},
                parameters={},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="lookup",
                value_field="value",
                target_column="distance_km",
            ),
            OutputMapping(
                step_id="lookup",
                value_field="unit",
                target_column="distance_unit",
            ),
        ],
        assumptions=[],
    )

    operations = (
        OperationResult(
            status="success",
            value=Quantity(value=100.5, unit="km"),
        ),
        OperationResult(
            status="failure",
            error_code="NOT_FOUND",
            message="Distance unavailable",
        ),
        OperationResult(
            status="success",
            value=Quantity(value=200.25, unit="km"),
        ),
    )

    job_result = JobExecutionResult(
        records=tuple(
            RecordExecution(
                record_id=record.record_id,
                row_result=RowExecutionResult(
                    steps=(
                        StepExecutionResult(
                            step_id="lookup",
                            result=operation,
                        ),
                    ),
                ),
            )
            for record, operation in zip(
                batch.records,
                operations,
                strict=True,
            )
        ),
        metrics=JobExecutionMetrics(
            total_rows=3,
            successful_rows=2,
            failed_rows=1,
            logical_operations=3,
            executed_handler_calls=3,
        ),
    )

    output_columns = {
        "distance_km": "G",
        "distance_unit": "H",
    }

    header_writes = build_output_header_writes(
        plan,
        selection,
        output_columns=output_columns,
    )

    value_writes = build_output_value_writes(
        plan,
        job_result,
        selection,
        row_by_record_id=batch.row_by_record_id,
        output_columns=output_columns,
    )

    write_excel_cells(
        source,
        output,
        sheet_name=selection.sheet_name,
        writes={**header_writes, **value_writes},
    )

    assert source.read_bytes() == original_bytes

    enriched = load_workbook(output, data_only=False)

    try:
        result = enriched["Cities!2026"]

        # Output headers.
        assert result["G6"].value == "distance_km"
        assert result["H6"].value == "distance_unit"

        # Successful results.
        assert result["G7"].value == 100.5
        assert result["H7"].value == "km"
        assert result["G11"].value == 200.25
        assert result["H11"].value == "km"

        # Failure and empty source rows remain unfilled.
        assert result["G9"].value is None
        assert result["H9"].value is None
        assert result["G8"].value is None
        assert result["G10"].value is None

        # Unrelated source data and formulas are preserved.
        assert result["D7"].value == "=1+2"
        assert result["F7"].value == "Preserve me"
        assert result["B6"].font.bold is True
        assert enriched["Notes"]["A1"].value == "Original notes"

    finally:
        enriched.close()
