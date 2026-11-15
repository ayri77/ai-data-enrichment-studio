"""Preflight must reject occupied targets before provider execution."""

from collections.abc import Mapping
from pathlib import Path

import pytest
from openpyxl import Workbook

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.capability import (
    CapabilitySpec,
    DataType,
    InputSpec,
    OutputSpec,
)
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import JobExecutionPolicy
from ai_data_enrichment.execution.models import (
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.orchestration.excel_runner import (
    ExcelEnrichmentRunner,
)


class CountingElevationHandler:
    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        self.calls += 1
        return OperationResult(
            status="success",
            value=Quantity(value=100.0, unit="m"),
        )


def test_occupied_target_prevents_provider_execution(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.xlsx"
    output = tmp_path / "enriched.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None

    sheet["A1"] = "mountain"
    sheet["A2"] = "Everest"
    sheet["B2"] = "Existing value"

    workbook.save(source)
    workbook.close()

    original_bytes = source.read_bytes()

    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            id="test_elevation",
            version="1.0",
            description="Offline elevation fixture",
            inputs={
                "name": InputSpec(
                    type=DataType.TEXT,
                    description="Mountain name",
                ),
            },
            output=OutputSpec(
                type=DataType.QUANTITY,
                description="Elevation",
                fields={"value": DataType.NUMBER},
            ),
        )
    )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="elevation",
                capability="test_elevation",
                inputs={
                    "name": ColumnReference(
                        kind="column",
                        name="mountain",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="elevation",
                value_field="value",
                target_column="height_m",
            ),
        ],
        assumptions=[],
    )

    handler = CountingElevationHandler()

    executor = JobExecutor(
        handlers={"test_elevation": handler},
        selected_providers={"test_elevation": "fixture"},
        configuration_ids={"test_elevation": "fixture-v1"},
        policy=JobExecutionPolicy(),
    )

    runner = ExcelEnrichmentRunner(
        registry=registry,
        executor=executor,
    )

    with pytest.raises(
        ValueError,
        match="Target cell is not empty: B2",
    ):
        runner.run(
            source_path=source,
            output_path=output,
            selection=TableSelection("Sheet", "A1:A2"),
            plan=plan,
            output_columns={"height_m": "B"},
            dataset_id="mountains",
        )

    assert handler.calls == 0
    assert not output.exists()
    assert source.read_bytes() == original_bytes


def _two_output_plan() -> EnrichmentPlan:
    return EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="elevation",
                capability="test_elevation",
                inputs={"name": ColumnReference(kind="column", name="mountain")},
                parameters={},
            ),
        ],
        outputs=[
            OutputMapping(step_id="elevation", value_field="value", target_column="height_m"),
            OutputMapping(step_id="elevation", value_field="value", target_column="again_m"),
        ],
        assumptions=[],
    )


def _skipping_runner(handler: CountingElevationHandler) -> ExcelEnrichmentRunner:
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            id="test_elevation",
            version="1.0",
            description="Offline elevation fixture",
            inputs={"name": InputSpec(type=DataType.TEXT, description="Mountain name")},
            output=OutputSpec(
                type=DataType.QUANTITY,
                description="Elevation",
                fields={"value": DataType.NUMBER},
            ),
        )
    )
    executor = JobExecutor(
        handlers={"test_elevation": handler},
        selected_providers={"test_elevation": "fixture"},
        configuration_ids={"test_elevation": "fixture-v1"},
        policy=JobExecutionPolicy(),
    )
    return ExcelEnrichmentRunner(registry=registry, executor=executor)


def _table(path: Path, rows: list[tuple[str, object, object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["mountain", "height_m", "again_m"])
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    workbook.close()


def _run(
    runner: ExcelEnrichmentRunner,
    tmp_path: Path,
    *,
    preserve_existing_outputs: bool = False,
    replace_existing_outputs: bool = False,
):
    seen: list[tuple[int, int]] = []
    result = runner.run(
        source_path=tmp_path / "source.xlsx",
        output_path=tmp_path / "enriched.xlsx",
        selection=TableSelection("Sheet", "A1:C4"),
        plan=_two_output_plan(),
        output_columns={"height_m": "B", "again_m": "C"},
        dataset_id="mountains",
        preserve_existing_outputs=preserve_existing_outputs,
        replace_existing_outputs=replace_existing_outputs,
        on_record_done=lambda done, total: seen.append((done, total)),
    )
    return result, seen


ROWS = [("Everest", 1.0, 2.0), ("K2", 3.0, None), ("Lhotse", None, None)]


def test_a_kept_row_with_every_target_filled_is_not_executed(tmp_path: Path) -> None:
    from openpyxl import load_workbook

    _table(tmp_path / "source.xlsx", ROWS)
    handler = CountingElevationHandler()
    result, seen = _run(_skipping_runner(handler), tmp_path, preserve_existing_outputs=True)
    # Everest is complete and skipped; K2 has one empty cell and Lhotse two: both run in full.
    assert handler.calls == 2
    assert list(result.skipped_records) == ["Sheet!2"]
    assert dict(result.skipped_records["Sheet!2"]) == {"B2": 1.0, "C2": 2.0}
    assert result.job_result.metrics.total_rows == 2
    assert result.job_result.metrics.logical_operations == 2
    # Progress counts the executed rows only.
    assert seen[-1] == (2, 2)
    book = load_workbook(result.output_path)
    try:
        sheet = book["Sheet"]
        assert [sheet[c].value for c in ("B2", "C2", "B3", "C3", "B4", "C4")] == [
            1.0,
            2.0,
            3.0,
            100.0,
            100.0,
            100.0,
        ]
    finally:
        book.close()


def test_every_row_skipped_runs_nothing_and_still_publishes(tmp_path: Path) -> None:
    _table(tmp_path / "source.xlsx", [("Everest", 1.0, 2.0), ("K2", 3.0, "=1+1"), ("L", 5, 6)])
    handler = CountingElevationHandler()
    result, _ = _run(_skipping_runner(handler), tmp_path, preserve_existing_outputs=True)
    assert handler.calls == 0
    assert result.job_result.metrics.total_rows == 0
    assert len(result.skipped_records) == 3
    assert result.skipped_records["Sheet!3"]["C3"] == "=1+1"
    assert result.output_path.is_file()


def test_nothing_is_skipped_when_values_may_be_replaced(tmp_path: Path) -> None:
    # Replacing asks for new values for every row.
    _table(tmp_path / "source.xlsx", [("Everest", 1.0, 2.0), ("K2", 3.0, 4.0), ("L", 5.0, 6.0)])
    handler = CountingElevationHandler()
    result, _ = _run(
        _skipping_runner(handler),
        tmp_path,
        preserve_existing_outputs=True,
        replace_existing_outputs=True,
    )
    assert handler.calls == 3 and not result.skipped_records


def test_a_run_without_the_keep_choice_still_refuses_occupied_cells(tmp_path: Path) -> None:
    _table(tmp_path / "source.xlsx", ROWS)
    handler = CountingElevationHandler()
    with pytest.raises(ValueError, match="Target cell is not empty"):
        _run(_skipping_runner(handler), tmp_path)
    assert handler.calls == 0
