"""Audited JobExecutor integration: DAG, paired memo, cache and row-linked reports."""

from pathlib import Path

import pytest
from tests.support.http import StubJsonTransport

from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionPolicy
from ai_data_enrichment.execution.models import GeoPoint
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.open_meteo import OpenMeteoGeocodingHandler
from ai_data_enrichment.providers.open_meteo_resolution import OpenMeteoResolutionService
from ai_data_enrichment.resolution.models import ResolutionJobReport, ResolutionPolicy
from ai_data_enrichment.resolution.sqlite_cache import SQLiteResolutionCache


def _locations() -> list[dict[str, object]]:
    return [
        {
            "id": 2950159,
            "name": "Berlin",
            "country_code": "DE",
            "admin1": "State of Berlin",
            "feature_code": "PPLC",
            "population": 3500000,
            "latitude": 52.52437,
            "longitude": 13.41053,
        },
        {
            "id": 2950158,
            "name": "Berlin",
            "country_code": "DE",
            "admin1": "Schleswig-Holstein",
            "feature_code": "PPL",
            "latitude": 54.033,
            "longitude": 10.45,
        },
    ]


def _plan(*, region: bool = False) -> EnrichmentPlan:
    refs = {
        "city": ColumnReference(kind="column", name="City"),
        "country": ColumnReference(kind="column", name="Country"),
    }
    if region:
        refs["administrative_area"] = ColumnReference(kind="column", name="Bundesland")
    return EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="geocode",
                capability="geocode",
                inputs=refs,
                parameters={},
            ),
            PlanStep(
                id="zero_distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(kind="step_output", step_id="geocode"),
                    "point_b": StepOutputReference(kind="step_output", step_id="geocode"),
                },
                parameters={"unit": "km"},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="zero_distance",
                value_field="value",
                target_column="DistanceKm",
            )
        ],
        assumptions=[],
    )


def _policy(*, version: str = "1", strict: bool = False) -> ResolutionPolicy:
    return ResolutionPolicy(
        policy_id="strict_geo" if strict else "ranked_geo",
        version=version,
        strategy="strict" if strict else "ranked",
        auto_accept_ranked=not strict,
        prefer_prominent=not strict,
    )


def _job(
    transport: StubJsonTransport,
    *,
    policy: ResolutionPolicy | None = None,
    persistent_cache: SQLiteResolutionCache | None = None,
    config: str = "geo-v1",
    provider_version: str = "openmeteo-v1",
    memoize: bool = True,
) -> JobExecutor:
    return JobExecutor(
        handlers={
            "geocode": OpenMeteoGeocodingHandler(transport),
            "geodesic_distance": GeodesicDistanceHandler(),
        },
        selected_providers={"geocode": "open-meteo", "geodesic_distance": "local"},
        configuration_ids={"geocode": config, "geodesic_distance": "hav-v1"},
        capability_versions={"geocode": "1.1"},
        provider_versions={"geocode": provider_version},
        policy=JobExecutionPolicy(
            max_rows=1000,
            batch_size=33,
            memoizable_providers=(
                frozenset({("geocode", "open-meteo")}) if memoize else frozenset()
            ),
            persistent_cache_ttls=(
                {("geocode", "open-meteo"): 120.0}
                if persistent_cache is not None and memoize
                else {}
            ),
        ),
        resolution_services={"geocode": OpenMeteoResolutionService(transport)},
        resolution_policies={"geocode": policy or _policy()},
        resolution_cache=persistent_cache,
    )


def _record(i: int, *, state: str | None = None) -> InputRecord:
    values = {"City": "Berlin", "Country": "Germany"}
    if state is not None:
        values["Bundesland"] = state
    return InputRecord(record_id=f"row-{i}", values=values)


def test_ranked_job_has_audited_capital_selection_and_downstream_dependency() -> None:
    transport = StubJsonTransport({"results": _locations()})
    result = _job(transport).execute(
        _plan(),
        [_record(1), _record(2)],
        job_id="job-Berlin-1",
    )
    assert result.metrics.total_rows == 2
    assert result.metrics.successful_rows == 2
    assert result.metrics.logical_operations == 4
    assert result.metrics.executed_handler_calls == 3
    assert result.metrics.reused_operations == 1
    assert len(transport.requests) == 1
    assert result.resolution_report is not None
    assert result.resolution_report.status == "completed"
    assert len(result.resolution_report.operations) == 1
    audited = result.resolution_report.operations[0]
    assert audited.report.decision.status == "auto_resolved"
    assert audited.report.decision.selected is not None
    assert audited.report.decision.selected.entity_id == "2950159"
    assert len(audited.report.candidate_set.candidates) == 2
    assert {(x.record_id, x.step_id) for x in audited.record_links} == {
        ("row-1", "geocode"),
        ("row-2", "geocode"),
    }
    assert all(
        rec.row_result.steps[0].result.value == GeoPoint(latitude=52.52437, longitude=13.41053)
        for rec in result.records
    )
    assert all(rec.row_result.steps[1].result.status == "success" for rec in result.records)
    assert ResolutionJobReport.model_validate_json(
        result.resolution_report.model_dump_json()
    ) == result.resolution_report


def test_strict_ambiguity_is_grouped_but_not_cached_as_success() -> None:
    transport = StubJsonTransport({"results": _locations()})
    result = _job(transport, policy=_policy(strict=True)).execute(
        _plan(),
        [_record(1), _record(2), _record(3)],
    )
    assert result.metrics.failed_rows == 3
    assert result.metrics.executed_handler_calls == 3
    assert result.metrics.reused_operations == 0
    assert len(transport.requests) == 3
    assert all(
        r.row_result.steps[1].result.error_code == "DEPENDENCY_FAILED"
        for r in result.records
    )
    assert result.resolution_report is not None
    assert result.resolution_report.status == "awaiting_review"
    assert len(result.resolution_report.operations) == 1
    assert len(result.resolution_report.operations[0].record_links) == 3
    assert result.resolution_report.operations[0].report.decision.status == "needs_review"


def test_1000_rows_preserve_two_region_decisions_and_source_provenance() -> None:
    transport = StubJsonTransport({"results": _locations()})
    rows = [
        _record(i, state="Berlin" if i % 2 == 0 else "Schleswig-Holstein")
        for i in range(1000)
    ]
    result = _job(transport).execute(_plan(region=True), rows, job_id="bench-1000")
    assert result.metrics.successful_rows == 1000
    assert result.metrics.logical_operations == 2000
    assert result.metrics.executed_handler_calls == 1002
    assert result.metrics.reused_operations == 998
    assert len(transport.requests) == 2
    assert result.resolution_report is not None
    assert len(result.resolution_report.operations) == 2
    assert sorted(
        len(item.record_links) for item in result.resolution_report.operations
    ) == [500, 500]
    assert sum(len(item.record_links) for item in result.resolution_report.operations) == 1000
    assert {item.report.decision.selected.entity_id for item in result.resolution_report.operations
            if item.report.decision.selected is not None} == {"2950158", "2950159"}
    assert result.resolution_report.status == "completed"


def test_paired_sqlite_cache_reuses_provenance_across_jobs_and_policy_versions(
    tmp_path: Path,
) -> None:
    transport = StubJsonTransport({"results": _locations()})
    with SQLiteResolutionCache(tmp_path / "audited.db") as cache:
        first = _job(transport, persistent_cache=cache).execute(
            _plan(), [_record(1)], job_id="job-1"
        )
        second = _job(transport, persistent_cache=cache).execute(
            _plan(), [_record(1)], job_id="job-2", previous_job_id="job-1"
        )
        third = _job(
            transport, persistent_cache=cache, policy=_policy(version="2")
        ).execute(_plan(), [_record(1)], job_id="job-3")
        fourth = _job(
            transport, persistent_cache=cache, config="geo-v2"
        ).execute(_plan(), [_record(1)], job_id="job-4")
        fifth = _job(
            transport, persistent_cache=cache, provider_version="openmeteo-v2"
        ).execute(_plan(), [_record(1)], job_id="job-5")
    assert len(transport.requests) == 4
    assert first.metrics.persistent_cache_writes == 1
    assert second.metrics.persistent_cache_hits == 1
    assert second.metrics.executed_handler_calls == 1  # downstream local step
    assert third.metrics.persistent_cache_misses == 1
    assert fourth.metrics.persistent_cache_misses == 1
    assert fifth.metrics.persistent_cache_misses == 1
    assert first.resolution_report is not None
    assert second.resolution_report is not None
    assert (
        first.resolution_report.operations[0].report
        == second.resolution_report.operations[0].report
    )
    assert second.resolution_report.previous_job_id == "job-1"
    assert first.resolution_report.operations[0].operation_id == (
        second.resolution_report.operations[0].operation_id
    )


def test_explicit_state_is_in_policy_aware_key_and_wins_over_prominence() -> None:
    transport = StubJsonTransport({"results": _locations()})
    result = _job(transport).execute(
        _plan(region=True),
        [_record(1, state="Berlin"), _record(2, state="Schleswig-Holstein")],
    )
    assert result.metrics.executed_handler_calls == 4
    assert result.metrics.reused_operations == 0
    assert result.resolution_report is not None
    assert {a.report.decision.selected.entity_id for a in result.resolution_report.operations
            if a.report.decision.selected is not None} == {"2950159", "2950158"}
    assert len(transport.requests) == 2


def test_no_opt_in_audited_handlers_preserves_legacy_strict_job() -> None:
    transport = StubJsonTransport({"results": _locations()})
    legacy = JobExecutor(
        handlers={
            "geocode": OpenMeteoGeocodingHandler(transport),
            "geodesic_distance": GeodesicDistanceHandler(),
        },
        selected_providers={"geocode": "open-meteo", "geodesic_distance": "local"},
        configuration_ids={"geocode": "legacy", "geodesic_distance": "local"},
        policy=JobExecutionPolicy(),
    )
    result = legacy.execute(_plan(), [_record(1)])
    assert result.resolution_report is None
    assert result.records[0].row_result.steps[0].result.error_code == "AMBIGUOUS"
    assert result.records[0].row_result.steps[1].result.error_code == "DEPENDENCY_FAILED"


def test_incomplete_resolution_configuration_rejected_before_provider_execution() -> None:
    transport = StubJsonTransport({"results": _locations()})
    with pytest.raises(ValueError, match="matching policies"):
        job = _job(transport)
        job._resolution_policies = {}
        job.execute(_plan(), [_record(1)])
    assert not transport.requests


def test_failed_unverified_candidate_stays_audit_failure() -> None:
    raw = _locations() + [
        {
            "id": 777,
            "name": "Berlin",
            "country_code": "DE",
            "latitude": 50.0,
            "longitude": 10.0,
        }
    ]
    transport = StubJsonTransport({"results": raw})
    result = _job(transport).execute(_plan(), [_record(1)])
    assert result.metrics.failed_rows == 1
    assert result.resolution_report is not None
    assert result.resolution_report.status == "awaiting_review"
    entry = result.resolution_report.operations[0]
    assert entry.report.decision.selected is None
    assert entry.report.decision.status == "needs_review"


def test_audited_persistent_cache_requires_separate_resolved_cache() -> None:
    transport = StubJsonTransport({"results": _locations()})
    job = _job(transport)
    job._policy = JobExecutionPolicy(
        memoizable_providers=frozenset({("geocode", "open-meteo")}),
        persistent_cache_ttls={("geocode", "open-meteo"): 60.0},
    )
    with pytest.raises(ValueError, match="separate paired cache"):
        job.execute(_plan(), [_record(1)])
    assert not transport.requests
