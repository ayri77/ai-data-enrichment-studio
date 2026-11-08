"""Academic Mountains: existing empty output column acceptance."""

from collections.abc import Mapping
from pathlib import Path
from shutil import copyfile

import pytest
from openpyxl import load_workbook

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan, OutputMapping
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import JobExecutionPolicy
from ai_data_enrichment.execution.models import (
    EntityRef,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.orchestration.excel_runner import ExcelEnrichmentRunner

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "samples" / "mountains.xlsx"


class OfflineMountainResolver:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        self.calls.append("resolve")

        if inputs["name"] != "Mount Everest":
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="Not present in the offline fixture.",
            )

        return OperationResult(
            status="success",
            value=EntityRef(source="fixture", entity_id="everest"),
        )


class OfflineElevationLookup:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        self.calls.append("lookup")

        return OperationResult(
            status="success",
            value=Quantity(value=8848.86, unit="m"),
        )


def make_runner(calls: list[str]) -> ExcelEnrichmentRunner:
    registry = CapabilityRegistry()

    for capability in get_builtin_capabilities():
        registry.register(capability)

    executor = JobExecutor(
        handlers={
            "resolve_entity": OfflineMountainResolver(calls),
            "lookup_property": OfflineElevationLookup(calls),
        },
        selected_providers={
            "resolve_entity": "offline",
            "lookup_property": "offline",
        },
        configuration_ids={
            "resolve_entity": "academic-fixture",
            "lookup_property": "academic-fixture",
        },
        policy=JobExecutionPolicy(max_rows=10, batch_size=10),
    )

    return ExcelEnrichmentRunner(
        registry=registry,
        executor=executor,
        max_rows=10,
    )


def make_plan() -> EnrichmentPlan:
    plan = EnrichmentPlan.model_validate_json(
        (ROOT / "demo" / "mountains_plan.json").read_text(encoding="utf-8")
    )

    return plan.model_copy(
        update={
            "outputs": [
                OutputMapping(
                    step_id="elevation",
                    value_field="value",
                    target_column="height",
                    expected_unit="m",
                )
            ]
        }
    )


def run_mountains(
    runner: ExcelEnrichmentRunner,
    source: Path,
    output: Path,
):
    return runner.run(
        source_path=source,
        output_path=output,
        selection=TableSelection("Sheet1", "A1:C11"),
        plan=make_plan(),
        output_columns={"height": "C"},
        dataset_id="academic-mountains",
    )


def test_fills_existing_height_column_without_changing_source(
    tmp_path: Path,
) -> None:
    original_bytes = SOURCE.read_bytes()
    output = tmp_path / "mountains_enriched.xlsx"
    calls: list[str] = []

    result = run_mountains(make_runner(calls), SOURCE, output)

    assert result.job_result.metrics.total_rows == 10
    assert result.job_result.metrics.successful_rows == 1
    assert calls

    workbook = load_workbook(output)
    try:
        sheet = workbook["Sheet1"]

        assert sheet["C1"].value == "height"
        assert sheet["A2"].value == "Mount Everest"
        assert sheet["C2"].value == pytest.approx(8848.86)

        # The other rows are deliberately unresolved in this fixture.
        assert all(sheet[f"C{row}"].value is None for row in range(3, 12))
    finally:
        workbook.close()

    assert SOURCE.read_bytes() == original_bytes


def test_rejects_occupied_height_cell_before_execution(
    tmp_path: Path,
) -> None:
    source = tmp_path / "occupied.xlsx"
    output = tmp_path / "should_not_exist.xlsx"
    copyfile(SOURCE, source)

    workbook = load_workbook(source)
    try:
        workbook["Sheet1"]["C3"] = 123.0
        workbook.save(source)
    finally:
        workbook.close()

    source_bytes = source.read_bytes()
    calls: list[str] = []

    with pytest.raises(ValueError, match="Target cell is not empty: C3"):
        run_mountains(make_runner(calls), source, output)

    assert calls == []
    assert not output.exists()
    assert source.read_bytes() == source_bytes
