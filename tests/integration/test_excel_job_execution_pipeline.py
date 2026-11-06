"""End-to-end Excel enrichment with deterministic offline providers."""

from collections.abc import Mapping
from pathlib import Path

from openpyxl import Workbook, load_workbook

from ai_data_enrichment.capabilities.definitions import (
    get_lookup_property_spec,
    get_resolve_entity_spec,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
    StepOutputReference,
    required_input_columns,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import JobExecutionPolicy
from ai_data_enrichment.execution.models import (
    EntityRef,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.ingestion.excel_records import read_input_batch
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.output.excel_output_mapping import (
    build_output_header_writes,
    build_output_value_writes,
)
from ai_data_enrichment.output.excel_writer import write_excel_cells
from ai_data_enrichment.providers.registry import ProviderRegistry
from ai_data_enrichment.validation.plan_validator import PlanValidator
from ai_data_enrichment.orchestration.excel_runner import (
    ExcelEnrichmentRunner,
)


class FixtureResolver:
    """Resolve test mountain names without external requests."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        assert parameters == {"entity_type": "mountain"}
        assert inputs["country"] == "Nepal"

        name = inputs["name"]
        assert isinstance(name, str)
        self.calls.append(name)

        ids = {
            "Mountain 0": "Q1000",
            "Mountain 1": "Q1001",
            "No Elevation": "Q1999",
        }

        entity_id = ids.get(name)

        if entity_id is None:
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="Mountain not found",
            )

        return OperationResult(
            status="success",
            value=EntityRef(
                source="wikidata",
                entity_id=entity_id,
            ),
        )


class FixtureElevationLookup:
    """Return deterministic elevations for resolved entities."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        assert parameters == {"property": "elevation"}

        entity = inputs["entity"]
        assert isinstance(entity, EntityRef)

        self.calls.append(entity.entity_id)

        if entity.entity_id == "Q1999":
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="Elevation unavailable",
            )

        return OperationResult(
            status="success",
            value=Quantity(
                value=float(int(entity.entity_id[1:])),
                unit="m",
            ),
        )


def test_real_job_executor_writes_enriched_excel(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mountains.xlsx"
    output = tmp_path / "enriched.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Mountains!2026"

    sheet["B6"] = "mountain"
    sheet["C6"] = "country"
    sheet["D6"] = "calculation"

    sheet["B7"] = "Mountain 0"
    sheet["C7"] = "Nepal"
    sheet["D7"] = "=1+2"

    # Row 8 intentionally empty.
    sheet["B9"] = "No Elevation"
    sheet["C9"] = "Nepal"

    # Row 10 intentionally empty.
    sheet["B11"] = "Mountain 1"
    sheet["C11"] = "Nepal"

    sheet["E7"] = "Unrelated data"

    notes = workbook.create_sheet("Notes")
    notes["A1"] = "Preserve this worksheet"

    workbook.save(source)
    workbook.close()

    original_bytes = source.read_bytes()

    selection = TableSelection(
        "Mountains!2026",
        "B6:D11",
    )

    profile = profile_worksheet(
        path=source,
        sheet_name=selection.sheet_name,
        dataset_id="mountains-fixture",
        selection=selection,
    )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": ColumnReference(
                        kind="column",
                        name="mountain",
                    ),
                    "country": ColumnReference(
                        kind="column",
                        name="country",
                    ),
                },
                parameters={"entity_type": "mountain"},
            ),
            PlanStep(
                id="lookup",
                capability="lookup_property",
                inputs={
                    "entity": StepOutputReference(
                        kind="step_output",
                        step_id="resolve",
                    ),
                },
                parameters={"property": "elevation"},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="lookup",
                value_field="value",
                target_column="elevation_m",
            ),
            OutputMapping(
                step_id="lookup",
                value_field="unit",
                target_column="elevation_unit",
            ),
        ],
        assumptions=[],
    )

    capabilities = CapabilityRegistry()
    capabilities.register(get_resolve_entity_spec())
    capabilities.register(get_lookup_property_spec())

    validation = PlanValidator(
        registry=capabilities,
        dataset_profile=profile,
    ).validate(plan)

    assert validation.valid, validation.errors

    batch = read_input_batch(
        source,
        selection=selection,
        input_columns=required_input_columns(plan),
    )

    assert len(batch.records) == 3

    resolver = FixtureResolver()
    lookup = FixtureElevationLookup()

    providers = ProviderRegistry(capabilities)
    providers.register(
        capability_id="resolve_entity",
        provider_id="fixture",
        handler=resolver,
    )
    providers.register(
        capability_id="lookup_property",
        provider_id="fixture",
        handler=lookup,
    )

    selected = {
        "resolve_entity": "fixture",
        "lookup_property": "fixture",
    }

    executor = JobExecutor(
        handlers=providers.resolve_handlers(
            capability_ids=[step.capability for step in plan.steps],
            selected_providers=selected,
        ),
        selected_providers=selected,
        configuration_ids={
            "resolve_entity": "fixture-v1",
            "lookup_property": "fixture-v1",
        },
        policy=JobExecutionPolicy(
            max_rows=10,
            batch_size=2,
            max_memoized_results=0,
        ),
    )

    runner = ExcelEnrichmentRunner(
        registry=capabilities,
        executor=executor,
        max_rows=10,
    )

    run_result = runner.run(
        source_path=source,
        output_path=output,
        selection=selection,
        plan=plan,
        output_columns={
            "elevation_m": "F",
            "elevation_unit": "G",
        },
        dataset_id="mountains-fixture",
    )

    job_result = run_result.job_result

    assert run_result.output_path == output

    assert job_result.metrics.total_rows == 3
    assert job_result.metrics.successful_rows == 2
    assert job_result.metrics.partial_rows == 1
    assert job_result.metrics.failed_rows == 0

    assert resolver.calls == [
        "Mountain 0",
        "No Elevation",
        "Mountain 1",
    ]
    assert lookup.calls == ["Q1000", "Q1999", "Q1001"]

    output_columns = {
        "elevation_m": "F",
        "elevation_unit": "G",
    }

    assert source.read_bytes() == original_bytes

    enriched = load_workbook(output, data_only=False)

    try:
        result = enriched[selection.sheet_name]

        assert result["F6"].value == "elevation_m"
        assert result["G6"].value == "elevation_unit"

        assert result["F7"].value == 1000.0
        assert result["G7"].value == "m"

        # Failed lookup does not become a successful value.
        assert result["F9"].value is None
        assert result["G9"].value is None

        assert result["F11"].value == 1001.0
        assert result["G11"].value == "m"

        # Blank source rows remain blank in output.
        assert result["F8"].value is None
        assert result["F10"].value is None

        # Unrelated content survives.
        assert result["D7"].value == "=1+2"
        assert result["E7"].value == "Unrelated data"
        assert enriched["Notes"]["A1"].value == "Preserve this worksheet"

    finally:
        enriched.close()
