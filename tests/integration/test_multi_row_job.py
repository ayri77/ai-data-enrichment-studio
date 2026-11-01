"""Offline multi-row DAG execution and bounded 1,000-record acceptance tests."""

import sqlite3
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from ai_data_enrichment.capabilities.definitions import (
    get_geocode_spec,
    get_geodesic_distance_spec,
    get_lookup_property_spec,
    get_resolve_entity_spec,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    LiteralValue,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionPolicy
from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.registry import ProviderRegistry
from ai_data_enrichment.validation.plan_validator import PlanValidator


class MountainResolver:
    """Deterministic entity resolver with explicit missing entities."""

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

        if name == "Unknown Mountain":
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="No trusted mountain found.",
            )

        if name == "No Elevation":
            qid = "Q1999"
        else:
            assert name.startswith("Mountain ")
            qid = f"Q{1000 + int(name.removeprefix('Mountain '))}"

        return OperationResult(
            status="success",
            value=EntityRef(source="wikidata", entity_id=qid),
            evidence=(Evidence(source="wikidata", reference=f"{qid}:P31"),),
        )


class ElevationLookup:
    """Return a typed and evidenced elevation for resolved entities."""

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
        assert entity.source == "wikidata"
        self.calls.append(entity.entity_id)
        if entity.entity_id == "Q1999":
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="Mountain elevation not available.",
            )
        return OperationResult(
            status="success",
            value=Quantity(
                value=float(int(entity.entity_id[1:])),
                unit="m",
            ),
            evidence=(Evidence(source="wikidata", reference=f"{entity.entity_id}:P2044"),),
        )


def mountain_setup(
    *, batch_size: int = 37, max_rows: int = 1000, capacity: int = 100
) -> tuple[JobExecutor, EnrichmentPlan, MountainResolver, ElevationLookup]:
    capabilities = CapabilityRegistry()
    capabilities.register(get_resolve_entity_spec())
    capabilities.register(get_lookup_property_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            # Deliberately reverse the dependency order.
            PlanStep(
                id="lookup",
                capability="lookup_property",
                inputs={"entity": StepOutputReference(kind="step_output", step_id="resolve")},
                parameters={"property": "elevation"},
            ),
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": ColumnReference(kind="column", name="mountain"),
                    "country": ColumnReference(kind="column", name="country"),
                },
                parameters={"entity_type": "mountain"},
            ),
        ],
        outputs=[OutputMapping(step_id="lookup", value_field="value", target_column="elevation_m")],
        assumptions=[],
    )
    profile = DatasetProfile(
        dataset_id="mountains",
        name="Synthetic Mountains",
        columns=[
            ColumnProfile(name="mountain", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="country", inferred_type=PrimitiveType.TEXT, null_count=0),
        ],
    )
    validation = PlanValidator(registry=capabilities, dataset_profile=profile).validate(plan)
    assert validation.valid, validation.errors

    resolver = MountainResolver()
    lookup = ElevationLookup()
    providers = ProviderRegistry(capabilities)
    providers.register(capability_id="resolve_entity", provider_id="wikidata", handler=resolver)
    providers.register(capability_id="lookup_property", provider_id="wikidata", handler=lookup)
    selection = {"resolve_entity": "wikidata", "lookup_property": "wikidata"}
    handlers = providers.resolve_handlers(
        capability_ids=[step.capability for step in plan.steps],
        selected_providers=selection,
    )

    executor = JobExecutor(
        handlers=handlers,
        selected_providers=selection,
        configuration_ids={
            "resolve_entity": "wikidata-fixture-v1",
            "lookup_property": "wikidata-fixture-v1",
        },
        policy=JobExecutionPolicy(
            max_rows=max_rows,
            batch_size=batch_size,
            max_memoized_results=capacity,
            memoizable_providers=frozenset(
                {("resolve_entity", "wikidata"), ("lookup_property", "wikidata")}
            ),
        ),
    )
    return executor, plan, resolver, lookup


def record(row_id: str, name: str | None, country: str | None = "Nepal") -> InputRecord:
    return InputRecord(record_id=row_id, values={"mountain": name, "country": country})


def test_1000_rows_deduplicate_across_batch_boundaries() -> None:
    executor, plan, resolver, lookup = mountain_setup(batch_size=37)
    source = [record(f"row-{999 - i:04d}", f"Mountain {i % 10}") for i in range(1000)]
    before = [dict(item.values) for item in source]

    result = executor.execute(plan, iter(source))

    assert len(result.records) == 1000
    assert [item.record_id for item in result.records] == [item.record_id for item in source]
    assert [item.values for item in source] == before
    assert len(resolver.calls) == 10
    assert len(lookup.calls) == 10
    assert set(resolver.calls) == {f"Mountain {i}" for i in range(10)}

    metrics = result.metrics
    assert metrics.total_rows == 1000
    assert metrics.successful_rows == 1000
    assert metrics.partial_rows == metrics.failed_rows == 0
    assert metrics.logical_operations == 2000
    assert metrics.executed_handler_calls == 20
    assert metrics.reused_operations == 1980
    assert metrics.memoized_results == 20
    assert metrics.memo_capacity_skips == 0

    first = result.records[0].row_result.steps
    last = result.records[-1].row_result.steps
    assert [step.step_id for step in first] == ["resolve", "lookup"]
    assert first[-1].result.value == Quantity(value=1000.0, unit="m")
    assert last[-1].result.value == Quantity(value=1009.0, unit="m")
    assert first[0].result.evidence == (Evidence(source="wikidata", reference="Q1000:P31"),)
    assert last[-1].result.evidence == (Evidence(source="wikidata", reference="Q1009:P2044"),)


def test_row_failures_are_isolated_and_dependency_steps_blocked() -> None:
    executor, plan, resolver, lookup = mountain_setup(batch_size=2)
    inputs = [
        record("a", "Mountain 0"),
        record("b", "Unknown Mountain"),
        record("c", "No Elevation"),
        record("d", "Mountain 0", None),
        record("e", "Mountain 0"),
        record("f", "Unknown Mountain"),
    ]

    job = executor.execute(plan, inputs)
    assert [record.record_id for record in job.records] == list("abcdef")
    m = job.metrics
    assert (m.successful_rows, m.partial_rows, m.failed_rows) == (2, 1, 3)
    assert m.logical_operations == 8
    assert m.executed_handler_calls == 6
    assert m.reused_operations == 2
    assert m.memoized_results == 3
    assert resolver.calls.count("Unknown Mountain") == 2
    assert "Q1999" in lookup.calls

    missing = job.records[1].row_result.steps
    assert [step.result.error_code for step in missing] == ["NOT_FOUND", "DEPENDENCY_FAILED"]
    no_elevation = job.records[2].row_result.steps
    assert [step.result.status for step in no_elevation] == ["success", "failure"]
    assert no_elevation[0].result.evidence[0].reference == "Q1999:P31"
    no_country = job.records[3].row_result.steps
    assert [step.result.error_code for step in no_country] == ["MISSING_VALUE", "DEPENDENCY_FAILED"]
    assert len(lookup.calls) == 2


def test_each_execution_starts_new_memoization_scope() -> None:
    executor, plan, resolver, lookup = mountain_setup(batch_size=1)
    rows = [record("1", "Mountain 0"), record("2", "Mountain 0")]
    first = executor.execute(plan, rows)
    second = executor.execute(plan, rows)
    assert first.metrics.executed_handler_calls == 2
    assert second.metrics.executed_handler_calls == 2
    assert first.metrics.reused_operations == second.metrics.reused_operations == 2
    assert len(resolver.calls) == len(lookup.calls) == 2


def test_empty_job_has_consistent_zero_metrics() -> None:
    executor, plan, resolver, lookup = mountain_setup()
    job = executor.execute(plan, [])
    assert job.records == ()
    assert job.metrics.total_rows == 0
    assert job.metrics.logical_operations == 0
    assert resolver.calls == lookup.calls == []


@pytest.mark.parametrize("failure", ["duplicate", "over_limit"])
def test_bad_input_fails_before_any_handler_call(failure: str) -> None:
    executor, plan, resolver, lookup = mountain_setup(max_rows=2)

    def bad_rows() -> Iterator[InputRecord]:
        for identifier in ["x", "x"] if failure == "duplicate" else ["a", "b", "c"]:
            yield record(identifier, "Mountain 0")

    with pytest.raises(ValueError, match="Duplicate record ID|exceeds max_rows"):
        executor.execute(plan, bad_rows())
    assert resolver.calls == lookup.calls == []


def test_missing_binding_rejected_before_first_row() -> None:
    executor, plan, resolver, lookup = mountain_setup()
    broken = JobExecutor(
        handlers={"resolve_entity": resolver},
        selected_providers={"resolve_entity": "wikidata"},
        configuration_ids={"resolve_entity": "test-v1"},
        policy=JobExecutionPolicy(),
    )
    with pytest.raises(ValueError, match="Incomplete job provider bindings"):
        broken.execute(plan, [record("row-1", "Mountain 0")])
    assert resolver.calls == lookup.calls == []


def test_capacity_limit_applies_across_batches() -> None:
    executor, plan, resolver, lookup = mountain_setup(batch_size=1, capacity=2)
    rows = [
        record("a", "Mountain 0"),
        record("b", "Mountain 1"),
        record("c", "Mountain 0"),
        record("d", "Mountain 1"),
    ]
    result = executor.execute(plan, rows)
    # Only the first mountain's resolution/elevation fit in the cache.
    assert result.metrics.memoized_results == 2
    assert result.metrics.executed_handler_calls == 6
    assert result.metrics.reused_operations == 2
    assert result.metrics.memo_capacity_skips == 4
    assert len(resolver.calls) == len(lookup.calls) == 3


class CityGeocoder:
    """Offline city lookup used with the real local geodesic handler."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.locations = {
            "Berlin": GeoPoint(latitude=52.52437, longitude=13.41053),
            "Hamburg": GeoPoint(latitude=53.55073, longitude=9.99302),
        }

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        assert parameters == {}
        assert inputs["country"] == "Germany"
        city = inputs["city"]
        assert isinstance(city, str)
        self.calls.append(city)
        point = self.locations.get(city)
        if point is None:
            return OperationResult(status="failure", error_code="NOT_FOUND", message="No city.")
        return OperationResult(
            status="success",
            value=point,
            evidence=(Evidence(source="fixture-geocoding", reference=f"DE/{city}"),),
        )


def test_capitals_workflow_reuses_geocoding_and_calculation() -> None:
    capabilities = CapabilityRegistry()
    capabilities.register(get_geocode_spec())
    capabilities.register(get_geodesic_distance_spec())
    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(kind="step_output", step_id="origin"),
                    "point_b": StepOutputReference(kind="step_output", step_id="destination"),
                },
                parameters={"unit": "km"},
            ),
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="city_a"),
                    "country": LiteralValue(kind="literal", value="Germany"),
                },
                parameters={},
            ),
            PlanStep(
                id="destination",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="city_b"),
                    "country": LiteralValue(kind="literal", value="Germany"),
                },
                parameters={},
            ),
        ],
        outputs=[
            OutputMapping(step_id="distance", value_field="value", target_column="distance_km")
        ],
        assumptions=[],
    )
    profile = DatasetProfile(
        dataset_id="capitals",
        name="German cities fixture",
        columns=[
            ColumnProfile(name="city_a", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="city_b", inferred_type=PrimitiveType.TEXT, null_count=0),
        ],
    )
    validation = PlanValidator(registry=capabilities, dataset_profile=profile).validate(plan)
    assert validation.valid, validation.errors

    geocoder = CityGeocoder()
    registry = ProviderRegistry(capabilities)
    registry.register(capability_id="geocode", provider_id="stub", handler=geocoder)
    registry.register(
        capability_id="geodesic_distance",
        provider_id="haversine",
        handler=GeodesicDistanceHandler(),
    )
    selected = {"geocode": "stub", "geodesic_distance": "haversine"}
    executor = JobExecutor(
        handlers=registry.resolve_handlers(
            capability_ids=[step.capability for step in plan.steps],
            selected_providers=selected,
        ),
        selected_providers=selected,
        configuration_ids={"geocode": "stub-v1", "geodesic_distance": "earth-6371.0088"},
        policy=JobExecutionPolicy(
            batch_size=1,
            memoizable_providers=frozenset(
                {("geocode", "stub"), ("geodesic_distance", "haversine")}
            ),
        ),
    )
    rows = [
        InputRecord(record_id=f"{i}", values={"city_a": "Berlin", "city_b": "Hamburg"})
        for i in range(12)
    ]
    job = executor.execute(plan, rows)
    assert job.metrics.total_rows == 12
    assert job.metrics.successful_rows == 12
    assert job.metrics.logical_operations == 36
    assert job.metrics.executed_handler_calls == 3
    assert job.metrics.reused_operations == 33
    assert len(geocoder.calls) == 2

    distance = job.records[-1].row_result.steps[-1].result
    assert isinstance(distance.value, Quantity)
    assert distance.value.unit == "km"
    assert 250.0 < distance.value.value < 260.0
    assert distance.evidence[0].source == "calculation"


class _FakeCacheClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _persistent_mountain_setup(
    cache: SQLiteOperationCache,
    *,
    capacity: int = 10,
    ttl: float = 30.0,
    capability_versions: dict[str, str] | None = None,
    provider_versions: dict[str, str] | None = None,
) -> tuple[JobExecutor, EnrichmentPlan, MountainResolver, ElevationLookup]:
    _, plan, resolver, lookup = mountain_setup()
    bindings = frozenset(
        {("resolve_entity", "wikidata"), ("lookup_property", "wikidata")}
    )
    executor = JobExecutor(
        handlers={"resolve_entity": resolver, "lookup_property": lookup},
        selected_providers={"resolve_entity": "wikidata", "lookup_property": "wikidata"},
        configuration_ids={
            "resolve_entity": "wikidata-fixture-v1",
            "lookup_property": "wikidata-fixture-v1",
        },
        capability_versions=capability_versions or {
            "resolve_entity": "1.0",
            "lookup_property": "1.0",
        },
        provider_versions=provider_versions or {
            "resolve_entity": "ontology-v1",
            "lookup_property": "elevation-v1",
        },
        persistent_cache=cache,
        policy=JobExecutionPolicy(
            batch_size=1,
            max_memoized_results=capacity,
            memoizable_providers=bindings,
            persistent_cache_ttls={binding: ttl for binding in bindings},
        ),
    )
    return executor, plan, resolver, lookup


def test_persistent_cache_reuses_typed_dag_results_between_jobs(tmp_path: Path) -> None:
    path = tmp_path / "two-jobs.db"
    rows = [
        record("a", "Mountain 0"),
        record("b", "Mountain 1"),
        record("c", "Mountain 0"),
        record("d", "Mountain 1"),
    ]
    with SQLiteOperationCache(path, clock=_FakeCacheClock()) as cache:
        first, plan, resolver_1, lookup_1 = _persistent_mountain_setup(cache)
        outcome_1 = first.execute(plan, rows)
        assert (len(resolver_1.calls), len(lookup_1.calls)) == (2, 2)
        assert outcome_1.metrics.logical_operations == 8
        assert outcome_1.metrics.executed_handler_calls == 4
        assert outcome_1.metrics.reused_operations == 4
        assert outcome_1.metrics.persistent_cache_hits == 0
        assert outcome_1.metrics.persistent_cache_misses == 4
        assert outcome_1.metrics.persistent_cache_writes == 4

    # A fresh connection and new JobExecutor must still restore original Evidence.
    with SQLiteOperationCache(path, clock=_FakeCacheClock()) as cache:
        second, _, resolver_2, lookup_2 = _persistent_mountain_setup(cache)
        outcome_2 = second.execute(plan, rows)
        assert (len(resolver_2.calls), len(lookup_2.calls)) == (0, 0)
        assert outcome_2.records == outcome_1.records
        metrics = outcome_2.metrics
        assert metrics.logical_operations == 8
        assert metrics.executed_handler_calls == 0
        assert metrics.persistent_cache_hits == 4
        assert metrics.reused_operations == 4
        assert metrics.memoized_results == 4
        assert metrics.persistent_cache_misses == 0
        assert metrics.persistent_cache_writes == 0


def test_expired_cross_job_results_are_recomputed(tmp_path: Path) -> None:
    clock = _FakeCacheClock()
    with SQLiteOperationCache(tmp_path / "expires.db", clock=clock) as cache:
        first, plan, resolver_1, lookup_1 = _persistent_mountain_setup(cache, ttl=10.0)
        first.execute(plan, [record("first", "Mountain 0")])
        clock.now = 1010.0
        second, _, resolver_2, lookup_2 = _persistent_mountain_setup(cache, ttl=10.0)
        refreshed = second.execute(plan, [record("second", "Mountain 0")])
        assert len(resolver_1.calls) == len(lookup_1.calls) == 1
        assert len(resolver_2.calls) == len(lookup_2.calls) == 1
        assert refreshed.metrics.executed_handler_calls == 2
        assert refreshed.metrics.persistent_cache_misses == 2
        assert refreshed.metrics.persistent_cache_writes == 2
        assert refreshed.metrics.persistent_cache_hits == 0


def test_provider_semantic_version_change_invalidates_only_its_key(tmp_path: Path) -> None:
    with SQLiteOperationCache(tmp_path / "versions.db", clock=_FakeCacheClock()) as cache:
        first, plan, _, _ = _persistent_mountain_setup(cache)
        first.execute(plan, [record("a", "Mountain 0")])
        second, _, resolver, lookup = _persistent_mountain_setup(
            cache,
            provider_versions={
                "resolve_entity": "ontology-v2",
                "lookup_property": "elevation-v1",
            },
        )
        result = second.execute(plan, [record("b", "Mountain 0")])
        assert len(resolver.calls) == 1
        assert lookup.calls == []
        assert result.metrics.persistent_cache_hits == 1
        assert result.metrics.persistent_cache_misses == 1
        assert result.metrics.executed_handler_calls == 1


def test_corrupt_cached_results_fail_closed_and_are_replaced(tmp_path: Path) -> None:
    path = tmp_path / "corrupt-job.db"
    with SQLiteOperationCache(path, clock=_FakeCacheClock()) as cache:
        first, plan, _, _ = _persistent_mountain_setup(cache)
        first.execute(plan, [record("a", "Mountain 0")])
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE operation_cache SET result_json = '{corrupt-json'"
            )
        second, _, resolver, lookup = _persistent_mountain_setup(cache)
        result = second.execute(plan, [record("b", "Mountain 0")])
        assert len(resolver.calls) == len(lookup.calls) == 1
        assert result.metrics.persistent_cache_invalid_entries == 2
        assert result.metrics.persistent_cache_misses == 2
        assert result.metrics.persistent_cache_writes == 2
        assert result.records[0].row_result.steps[-1].result.evidence[0].source == "wikidata"


def test_provider_failure_is_not_persisted_between_jobs(tmp_path: Path) -> None:
    with SQLiteOperationCache(tmp_path / "failure.db", clock=_FakeCacheClock()) as cache:
        first, plan, resolver_1, _ = _persistent_mountain_setup(cache)
        first_outcome = first.execute(plan, [record("a", "Unknown Mountain")])
        second, _, resolver_2, lookup_2 = _persistent_mountain_setup(cache)
        second_outcome = second.execute(plan, [record("b", "Unknown Mountain")])
        assert len(resolver_1.calls) == len(resolver_2.calls) == 1
        assert lookup_2.calls == []
        assert first_outcome.metrics.persistent_cache_writes == 0
        assert second_outcome.metrics.persistent_cache_hits == 0
        assert second_outcome.metrics.persistent_cache_misses == 1
        assert second_outcome.records[0].row_result.steps[-1].result.error_code == (
            "DEPENDENCY_FAILED"
        )


def test_zero_memory_capacity_still_reuses_sqlite_between_jobs(tmp_path: Path) -> None:
    with SQLiteOperationCache(tmp_path / "no-memory.db", clock=_FakeCacheClock()) as cache:
        first, plan, _, _ = _persistent_mountain_setup(cache, capacity=0)
        first.execute(plan, [record("a", "Mountain 0")])
        second, _, resolver, lookup = _persistent_mountain_setup(cache, capacity=0)
        result = second.execute(plan, [record("b", "Mountain 0")])
        assert resolver.calls == lookup.calls == []
        assert result.metrics.persistent_cache_hits == 2
        assert result.metrics.reused_operations == 0
        assert result.metrics.memoized_results == 0
        assert result.metrics.memo_capacity_skips == 2


def test_cache_versions_must_be_configured_before_provider_calls(tmp_path: Path) -> None:
    with SQLiteOperationCache(tmp_path / "missing-version.db") as cache:
        _, plan, resolver, lookup = mountain_setup()
        broken = JobExecutor(
            handlers={"resolve_entity": resolver, "lookup_property": lookup},
            selected_providers={"resolve_entity": "wikidata", "lookup_property": "wikidata"},
            configuration_ids={
                "resolve_entity": "config-v1",
                "lookup_property": "config-v1",
            },
            persistent_cache=cache,
            policy=JobExecutionPolicy(
                memoizable_providers=frozenset({("resolve_entity", "wikidata")}),
                persistent_cache_ttls={("resolve_entity", "wikidata"): 20.0},
            ),
        )
        with pytest.raises(ValueError, match="Missing persistent cache versions"):
            broken.execute(plan, [record("row", "Mountain 0")])
        assert resolver.calls == lookup.calls == []


def test_unconfigured_cache_storage_does_not_override_policy(tmp_path: Path) -> None:
    with SQLiteOperationCache(tmp_path / "not-eligible.db") as cache:
        _, plan, resolver, lookup = mountain_setup()
        executor = JobExecutor(
            handlers={"resolve_entity": resolver, "lookup_property": lookup},
            selected_providers={"resolve_entity": "wikidata", "lookup_property": "wikidata"},
            configuration_ids={
                "resolve_entity": "config-v1",
                "lookup_property": "config-v1",
            },
            persistent_cache=cache,
            policy=JobExecutionPolicy(
                memoizable_providers=frozenset({("resolve_entity", "wikidata")}),
            ),
        )
        first = executor.execute(plan, [record("a", "Mountain 0")])
        second = executor.execute(plan, [record("b", "Mountain 0")])
        assert len(resolver.calls) == len(lookup.calls) == 2
        assert first.metrics.persistent_cache_hits == second.metrics.persistent_cache_hits == 0
        assert first.metrics.persistent_cache_writes == second.metrics.persistent_cache_writes == 0
