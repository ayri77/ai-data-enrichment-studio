"""Map logical output columns to explicit Excel coordinates."""

from collections.abc import Mapping

from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.execution.job_models import JobExecutionResult
from ai_data_enrichment.output.value_projection import project_operation_field


def build_output_header_writes(
    plan: EnrichmentPlan,
    selection: TableSelection,
    *,
    output_columns: Mapping[str, str],
) -> dict[str, str]:
    """Build header writes for explicitly assigned new output columns."""

    target_names = [
        output.target_column
        for output in plan.outputs
    ]

    if len(target_names) != len(set(target_names)):
        raise ValueError("Duplicate output target names")

    if set(output_columns) != set(target_names):
        raise ValueError(
            "Output column bindings must match plan output targets"
        )

    max_source_col = selection.bounds[2]
    used_columns: set[int] = set()
    writes: dict[str, str] = {}

    for target_name in target_names:
        letter = output_columns[target_name]

        try:
            column_index = column_index_from_string(letter)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid Excel column: {letter}"
            ) from exc

        if not 1 <= column_index <= 16_384:
            raise ValueError(
                f"Excel column out of range: {letter}"
            )

        if column_index <= max_source_col:
            raise ValueError(
                f"Output column overlaps selected table: {letter}"
            )

        if column_index in used_columns:
            raise ValueError(
                f"Duplicate Excel output column: {letter}"
            )

        used_columns.add(column_index)

        coordinate = (
            f"{get_column_letter(column_index)}"
            f"{selection.header_row}"
        )
        writes[coordinate] = target_name

    return writes


def build_output_value_writes(
    plan: EnrichmentPlan,
    job_result: JobExecutionResult,
    selection: TableSelection,
    *,
    row_by_record_id: Mapping[str, int],
    output_columns: Mapping[str, str],
) -> dict[str, str | float]:
    """Map successful job outputs to explicit Excel cell coordinates."""

    # Reuse the existing column-binding validation.
    build_output_header_writes(
        plan,
        selection,
        output_columns=output_columns,
    )

    record_ids = {
        record.record_id for record in job_result.records
    }

    if set(row_by_record_id) != record_ids:
        raise ValueError(
            "Row mapping must match job record IDs"
        )

    rows = list(row_by_record_id.values())

    if len(set(rows)) != len(rows):
        raise ValueError(
            "Multiple records map to the same Excel row"
        )

    max_row = selection.bounds[3]

    for record_id, row_number in row_by_record_id.items():
        if (
            type(row_number) is not int
            or not selection.first_data_row <= row_number <= max_row
        ):
            raise ValueError(
                f"Invalid Excel row for record {record_id}"
            )

    writes: dict[str, str | float] = {}

    for record in job_result.records:
        row_number = row_by_record_id[record.record_id]

        step_results = {
            step.step_id: step.result
            for step in record.row_result.steps
        }

        for output in plan.outputs:
            if output.step_id not in step_results:
                raise ValueError(
                    f"Missing step result: {output.step_id}"
                )

            result = step_results[output.step_id]

            # Failed operations produce no successful cell value.
            if result.status != "success":
                continue

            value = project_operation_field(
                result,
                output.value_field,
            )

            column_index = column_index_from_string(
                output_columns[output.target_column]
            )
            column_letter = get_column_letter(column_index)

            coordinate = f"{column_letter}{row_number}"

            if coordinate in writes:
                raise ValueError(
                    f"Duplicate output cell: {coordinate}"
                )

            writes[coordinate] = value

    return writes
