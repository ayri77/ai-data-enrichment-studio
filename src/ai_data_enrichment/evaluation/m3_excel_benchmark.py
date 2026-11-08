"""Offline end-to-end Excel benchmark for audited capital resolution."""

import tracemalloc
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from openpyxl import Workbook, load_workbook
from pydantic import BaseModel, ConfigDict

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.evaluation.benchmark_fixtures import (
    GeocodingFixture,
    load_geocoding_fixture,
)
from ai_data_enrichment.evaluation.benchmark_scenarios import BenchmarkScenario
from ai_data_enrichment.evaluation.offline_fixture_transport import (
    OfflineFixtureTransport,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import JobExecutionPolicy
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.orchestration.excel_runner import ExcelEnrichmentRunner
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.open_meteo import (
    OpenMeteoGeocodingHandler,
)
from ai_data_enrichment.providers.open_meteo_resolution import (
    OpenMeteoResolutionService,
)
from ai_data_enrichment.resolution.models import ResolutionPolicy


class M3ExcelBenchmarkResult(BaseModel):
    """Measured benchmark results and verified execution counters."""

    model_config = ConfigDict(frozen=True)

    rows: int
    elapsed_seconds: float
    rows_per_second: float
    logical_operations: int
    executed_handler_calls: int
    reused_operations: int
    transport_calls: int
    audit_operations: int
    audit_record_step_links: int
    successful_rows: int
    failed_rows: int
    output_rows_verified: int
    source_unchanged: bool
    peak_python_memory_bytes: int | None = None


def _build_plan(
    *,
    required_role: str | None = "country_capital",
) -> EnrichmentPlan:
    """Build a deterministic enrichment plan for city pairs."""

    return EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="capital_a",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="City A"),
                    "country": ColumnReference(kind="column", name="Country A"),
                },
                parameters=({"required_role": required_role} if required_role is not None else {}),
            ),
            PlanStep(
                id="capital_b",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="City B"),
                    "country": ColumnReference(kind="column", name="Country B"),
                },
                parameters=({"required_role": required_role} if required_role is not None else {}),
            ),
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(kind="step_output", step_id="capital_a"),
                    "point_b": StepOutputReference(kind="step_output", step_id="capital_b"),
                },
                parameters={"unit": "km"},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="distance",
                value_field="value",
                target_column="distance_km",
                expected_unit="km",
            ),
        ],
        assumptions=[],
    )


def _locations_from_fixture(
    fixture: GeocodingFixture,
) -> tuple[tuple[str, str], ...]:
    """Extract deterministic city/country pairs from a fixture."""

    locations = tuple(
        (
            response.query.name,
            response.query.countryCode,
        )
        for response in fixture.responses
    )

    if len(locations) < 2:
        raise ValueError("City-pair benchmark requires at least two locations.")

    return locations


def _expected_distances(
    fixture: GeocodingFixture,
    rows: int,
) -> tuple[float, ...]:
    """Calculate reference distances for round-robin city pairs.

    The fixture must contain exactly one candidate per city.
    This keeps the reference calculation unambiguous.
    """
    coordinates: list[tuple[float, float]] = []

    for response in fixture.responses:
        candidates = response.payload.results

        if len(candidates) != 1:
            raise ValueError("Unique cities fixture requires exactly one candidate per location.")

        point = candidates[0]
        coordinates.append((point.latitude, point.longitude))

    if len(coordinates) < 2:
        raise ValueError("At least two locations are required.")

    distances: list[float] = []

    for index in range(rows):
        lat_a, lon_a = coordinates[index % len(coordinates)]
        lat_b, lon_b = coordinates[(index + 1) % len(coordinates)]

        delta_lat = radians(lat_b - lat_a)
        delta_lon = radians(lon_b - lon_a)

        a = (
            sin(delta_lat / 2) ** 2
            + cos(radians(lat_a)) * cos(radians(lat_b)) * sin(delta_lon / 2) ** 2
        )

        a = min(1.0, max(0.0, a))

        distance = 2 * 6371.0088 * asin(sqrt(a))
        distances.append(distance)

    return tuple(distances)


def _create_source(
    path: Path,
    rows: int,
    *,
    locations: tuple[tuple[str, str], ...] | None = None,
) -> None:
    """Create a deterministic Excel source workbook.

    Without locations, preserve the original Capitals workload.

    With locations, generate consecutive city pairs in
    round-robin order.
    """
    if locations is not None and len(locations) < 2:
        raise ValueError("Unique cities workload requires at least two locations.")

    workbook = Workbook()

    try:
        sheet = workbook.active
        assert sheet is not None
        sheet.title = "Capitals"

        sheet.append(["City A", "Country A", "City B", "Country B"])

        for index in range(rows):
            if locations is None:
                # Preserve the existing Capitals benchmark.
                if index % 2 == 0:
                    city_a = ("Berlin", "Germany")
                    city_b = ("Kyiv", "Ukraine")
                else:
                    city_a = ("Kyiv", "Ukraine")
                    city_b = ("Berlin", "Germany")
            else:
                # Deterministic round-robin city pairs.
                city_a = locations[index % len(locations)]
                city_b = locations[(index + 1) % len(locations)]

            sheet.append(
                [
                    city_a[0],
                    city_a[1],
                    city_b[0],
                    city_b[1],
                ]
            )

        notes = workbook.create_sheet("Notes")
        notes["A1"] = "Preserve this worksheet"

        workbook.save(path)
    finally:
        workbook.close()


def run_benchmark(
    rows: int = 8,
    *,
    profile_memory: bool = False,
    memo_capacity: int | None = None,
    fixture_path: Path | None = None,
    dataset: str = "capitals",
    required_role: str | None = "country_capital",
) -> M3ExcelBenchmarkResult:
    """Run the complete Excel pipeline with controlled provider responses."""

    if type(rows) is not int or not 1 <= rows <= 1000:
        raise ValueError("Benchmark accepts 1 to 1000 rows.")

    if memo_capacity is None:
        memo_capacity = rows

    if type(memo_capacity) is not int or memo_capacity < 0:
        raise ValueError("Memo capacity must be a non-negative integer.")

    if fixture_path is None:
        fixture_path = (
            Path(__file__).resolve().parents[3] / "benchmarks" / "fixtures" / "capitals.json"
        )

    if dataset not in {"capitals", "unique_cities"}:
        raise ValueError(f"Unsupported benchmark dataset: {dataset}")

    if dataset == "capitals" and required_role != "country_capital":
        raise ValueError("Capitals require country_capital role.")

    if dataset == "unique_cities" and required_role is not None:
        raise ValueError("Unique cities require ordinary geocoding.")

    fixture = load_geocoding_fixture(fixture_path)
    transport = OfflineFixtureTransport(fixture)

    locations = _locations_from_fixture(fixture) if dataset == "unique_cities" else None

    expected_distances = _expected_distances(fixture, rows) if dataset == "unique_cities" else None

    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)

    executor = JobExecutor(
        handlers={
            "geocode": OpenMeteoGeocodingHandler(transport),
            "geodesic_distance": GeodesicDistanceHandler(),
        },
        selected_providers={
            "geocode": "open-meteo",
            "geodesic_distance": "local",
        },
        configuration_ids={
            "geocode": "m3-capitals-benchmark-v1",
            "geodesic_distance": "haversine-v1",
        },
        capability_versions={"geocode": "1.1"},
        provider_versions={"geocode": "open-meteo-v1"},
        policy=JobExecutionPolicy(
            max_rows=rows,
            batch_size=min(rows, 100),
            max_memoized_results=memo_capacity,
            memoizable_providers=frozenset(
                {
                    ("geocode", "open-meteo"),
                    ("geodesic_distance", "local"),
                }
            ),
        ),
        resolution_services={
            "geocode": OpenMeteoResolutionService(transport),
        },
        resolution_policies={
            "geocode": ResolutionPolicy(
                policy_id="capital_strict",
                version="1.0",
                strategy="strict",
            ),
        },
    )

    runner = ExcelEnrichmentRunner(
        registry=registry,
        executor=executor,
        max_rows=rows,
    )

    with TemporaryDirectory(prefix="m3-excel-benchmark-") as directory:
        root = Path(directory)
        source = root / "source.xlsx"
        output = root / "enriched.xlsx"

        _create_source(
            source,
            rows,
            locations=locations,
        )
        original_bytes = source.read_bytes()

        if profile_memory:
            tracemalloc.start()

        try:
            started = perf_counter()

            result = runner.run(
                source_path=source,
                output_path=output,
                selection=TableSelection(
                    sheet_name="Capitals",
                    range_ref=f"A1:D{rows + 1}",
                ),
                plan=_build_plan(required_role=required_role),
                output_columns={"distance_km": "E"},
                dataset_id="m3-capitals-benchmark",
            )

            elapsed = perf_counter() - started

            peak_memory = tracemalloc.get_traced_memory()[1] if profile_memory else None
        finally:
            if profile_memory:
                tracemalloc.stop()

        metrics = result.job_result.metrics
        report = result.job_result.resolution_report

        assert report is not None
        assert metrics.total_rows == rows
        assert metrics.successful_rows == rows
        assert metrics.failed_rows == 0
        assert metrics.logical_operations == 3 * rows

        if dataset == "capitals":
            expected_executions = 3 if rows == 1 else 4
            expected_transport_calls = 2
            expected_audit_operations = 2
        else:
            assert locations is not None

            # With sufficient memo capacity, the fixture contains
            # one unique geocode operation per location and one
            # distance operation per distinct directed city pair.
            expected_pairs = {
                (
                    locations[index % len(locations)],
                    locations[(index + 1) % len(locations)],
                )
                for index in range(rows)
            }
            used_locations = {location for pair in expected_pairs for location in pair}

            expected_transport_calls = len(used_locations)
            expected_audit_operations = len(used_locations)
            expected_executions = len(used_locations) + len(expected_pairs)

        if memo_capacity == 0:
            # All operations execute because memoization is disabled.
            assert metrics.executed_handler_calls == 3 * rows
            assert metrics.reused_operations == 0
            assert transport.calls == 2 * rows

            # Identical audit reports are deduplicated independently
            # of the operation-result memoization cache.
            assert len(report.operations) == expected_audit_operations
        else:
            # Exact expectations for sufficient memo capacity.
            required_capacity = expected_executions
            if memo_capacity < required_capacity:
                raise ValueError(
                    "Intermediate memo capacities are not supported by benchmark assertions yet."
                )

            assert metrics.executed_handler_calls == expected_executions
            assert metrics.reused_operations == (3 * rows - expected_executions)
            assert transport.calls == expected_transport_calls
            assert len(report.operations) == expected_audit_operations

        assert report.status == "completed"

        links = sum(len(operation.record_links) for operation in report.operations)
        assert links == 2 * rows
        verified_rows = 0

        workbook = load_workbook(output, read_only=True, data_only=False)
        try:
            sheet = workbook["Capitals"]

            assert sheet["E1"].value == "distance_km"

            for row_number in range(2, rows + 2):
                distance = sheet[f"E{row_number}"].value
                assert isinstance(distance, (int, float))
                expected_distance = (
                    expected_distances[row_number - 2]
                    if expected_distances is not None
                    else 1203.9890563606518
                )

                assert abs(distance - expected_distance) < 0.001
                verified_rows += 1

            assert workbook["Notes"]["A1"].value == ("Preserve this worksheet")
        finally:
            workbook.close()

        source_unchanged = source.read_bytes() == original_bytes
        assert source_unchanged

        return M3ExcelBenchmarkResult(
            rows=rows,
            elapsed_seconds=elapsed,
            rows_per_second=rows / elapsed,
            logical_operations=metrics.logical_operations,
            executed_handler_calls=metrics.executed_handler_calls,
            reused_operations=metrics.reused_operations,
            transport_calls=transport.calls,
            audit_operations=len(report.operations),
            audit_record_step_links=links,
            successful_rows=metrics.successful_rows,
            failed_rows=metrics.failed_rows,
            output_rows_verified=verified_rows,
            source_unchanged=source_unchanged,
            peak_python_memory_bytes=peak_memory,
        )


def run_configured_benchmark(
    scenario: BenchmarkScenario,
    *,
    profile_memory: bool = False,
) -> M3ExcelBenchmarkResult:
    """Execute a validated offline Excel benchmark scenario."""

    fixture_path = (
        Path(__file__).resolve().parents[3] / "benchmarks" / "fixtures" / scenario.fixture
    )

    return run_benchmark(
        rows=scenario.rows,
        profile_memory=profile_memory,
        memo_capacity=scenario.memo_capacity,
        fixture_path=fixture_path,
        dataset=scenario.dataset,
        required_role=scenario.required_role,
    )
