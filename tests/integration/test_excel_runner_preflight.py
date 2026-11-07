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
