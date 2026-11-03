"""Offline, deterministic M2 acceptance benchmark for both mandatory DAG plans.

No real network or LLM calls. Counts describe attempts admitted through the
actual request-budget/transport wrappers, not requests to remote servers.
Timing measures JobExecutor only (not plan validation or fixture construction).
Run on CI, preserve JSON output and do not treat host-dependent elapsed time
as a portable latency guarantee.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.evaluation.smoke import (
    OfflineWikidataTransport,
    _plan,
    _validate,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionPolicy
from ai_data_enrichment.execution.models import EntityRef, Quantity
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.http_transport import JsonTransport
from ai_data_enrichment.providers.open_meteo import GEOCODING_URL, OpenMeteoGeocodingHandler
from ai_data_enrichment.providers.open_meteo_resolution import OpenMeteoResolutionService
from ai_data_enrichment.providers.registry import ProviderRegistry
from ai_data_enrichment.providers.request_policy import (
    ProviderRequestPolicy,
    ProviderRequestSession,
)
from ai_data_enrichment.providers.retry_transport import TransportRetryPolicy
from ai_data_enrichment.providers.wikidata import (
    WIKIDATA_API_URL,
    WikidataElevationHandler,
    WikidataMountainResolver,
)
from ai_data_enrichment.providers.wikidata_resolution import WikidataMountainResolutionService
from ai_data_enrichment.resolution.models import ResolutionPolicy

Scenario = Literal["capitals", "mountains"]
Variant = Literal["normal", "strict", "unchecked_mountain"]


def _statement(qid: str) -> dict[str, object]:
    return {
        "rank": "normal",
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {"type": "wikibase-entityid", "value": {"id": qid}},
        },
    }


class BenchmarkFixtureTransport:
    """Real endpoint/params are checked, but no socket can ever be opened."""

    def __init__(self, *, variant: Variant = "normal") -> None:
        self.variant = variant
        self.attempts = 0
        self.wikidata = OfflineWikidataTransport()

    def get_json(
        self, *, url: str, params: Mapping[str, str], timeout_seconds: float
    ) -> object:
        self.attempts += 1
        if timeout_seconds != 10.0:
            raise ValueError("Unexpected timeout.")
        if url == GEOCODING_URL:
            if params["countryCode"] != "DE":
                raise ValueError("Unexpected geocoding country.")
            if params["name"] == "Hamburg":
                return {
                    "results": [
                        {
                            "id": 2911298,
                            "name": "Hamburg",
                            "country_code": "DE",
                            "feature_code": "PPL",
                            "latitude": 53.55073,
                            "longitude": 9.99302,
                        }
                    ]
                }
            if params["name"] == "Berlin":
                return {
                    "results": [
                        {
                            "id": 2950159,
                            "name": "Berlin",
                            "country_code": "DE",
                            "admin1": "State of Berlin",
                            "feature_code": "PPLC",
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
                }
            raise ValueError("Unexpected geocoding query.")
        if url == WIKIDATA_API_URL:
            if params["action"] == "wbsearchentities":
                if params["search"] != "Mount Everest":
                    raise ValueError("Unexpected mountain name.")
                hits: list[dict[str, object]] = [
                    {"id": "Q513", "label": "Mount Everest"},
                    {
                        "id": "Q61882061",
                        "label": "The Climbers",
                        "match": {"type": "alias", "text": "Mount Everest"},
                    },
                ]
                if self.variant == "unchecked_mountain":
                    hits.append(
                        {
                            "id": "Q9200",
                            "label": "Unverified Alias",
                            "match": {"type": "alias", "text": "Mount Everest"},
                        }
                    )
                return {"search": hits}
            if params["action"] == "wbgetentities" and params["ids"] == "Q61882061":
                return {
                    "entities": {
                        "Q61882061": {
                            "id": "Q61882061",
                            "claims": {"P31": [_statement("Q11424")]},
                        }
                    }
                }
            if params["action"] == "wbgetentities" and params["ids"] == "Q9200":
                return {"entities": {"Q9200": {"id": "Q9200", "claims": {}}}}
            return self.wikidata.get_json(url=url, params=params, timeout_seconds=timeout_seconds)
        raise ValueError("An unregistered provider URL was requested.")


@dataclass(frozen=True)
class BenchmarkResult:
    """Machine-readable measured result; status is enforced by assertions."""

    scenario: Scenario
    variant: Variant
    rows: int
    elapsed_seconds: float
    logical_operations: int
    executed_handler_calls: int
    reused_operations: int
    admitted_transport_attempts: int
    fixture_transport_calls: int
    successful_rows: int
    partial_rows: int
    failed_rows: int
    audit_operations: int
    audit_row_step_links: int
    resolution_decisions: dict[str, int]
    evidence_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "variant": self.variant,
            "rows": self.rows,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "logical_operations": self.logical_operations,
            "executed_handler_calls": self.executed_handler_calls,
            "reused_operations": self.reused_operations,
            "admitted_transport_attempts": self.admitted_transport_attempts,
            "fixture_transport_calls": self.fixture_transport_calls,
            "successful_rows": self.successful_rows,
            "partial_rows": self.partial_rows,
            "failed_rows": self.failed_rows,
            "audit_operations": self.audit_operations,
            "audit_row_step_links": self.audit_row_step_links,
            "resolution_decisions": self.resolution_decisions,
            "evidence_count": self.evidence_count,
        }


def run_scenario(
    scenario: Scenario,
    *,
    rows: int,
    variant: Variant = "normal",
) -> BenchmarkResult:
    """Verify a plan then time its real audited DAG and bounded fixture transport."""
    if type(rows) is not int or not 1 <= rows <= 1000:
        raise ValueError("Benchmark accepts 1 to 1000 rows.")
    if scenario == "capitals" and variant == "unchecked_mountain":
        raise ValueError("Unchecked mountain fixture applies only to mountains.")
    if scenario == "mountains" and variant == "strict":
        raise ValueError("Strict ambiguity fixture applies only to capitals.")

    if scenario == "capitals":
        records = [
            InputRecord(
                record_id=f"row-{i + 1}",
                values={
                    "city_a": "Hamburg" if i % 2 == 0 else "Berlin",
                    "country_a": "Germany",
                    "city_b": "Berlin" if i % 2 == 0 else "Hamburg",
                    "country_b": "Germany",
                },
            )
            for i in range(rows)
        ]
    else:
        records = [
            InputRecord(
                record_id=f"row-{i + 1}",
                values={"mountain": "Mount Everest", "country": "Nepal"},
            )
            for i in range(rows)
        ]

    plan = _plan(scenario)
    capabilities = _validate(plan, records)
    providers = ProviderRegistry(capabilities)
    source_id = "open-meteo" if scenario == "capitals" else "wikidata"
    binding = (
        {"geocode": source_id, "geodesic_distance": "local-haversine"}
        if scenario == "capitals"
        else {"resolve_entity": source_id, "lookup_property": source_id}
    )
    fixture = BenchmarkFixtureTransport(variant=variant)
    session = ProviderRequestSession(
        {
            source_id: ProviderRequestPolicy(
                max_requests_per_job=16,
                min_interval_seconds=0,
            )
        }
    )
    transport: JsonTransport = session.wrap_transport(
        source_id, fixture, retry_policy=TransportRetryPolicy(max_attempts=1)
    )

    if scenario == "capitals":
        providers.register(
            capability_id="geocode",
            provider_id=source_id,
            handler=OpenMeteoGeocodingHandler(transport),
        )
        providers.register(
            capability_id="geodesic_distance",
            provider_id="local-haversine",
            handler=GeodesicDistanceHandler(),
        )
        resolution_services = {"geocode": OpenMeteoResolutionService(transport)}
        resolution_policies = {
            "geocode": ResolutionPolicy(
                policy_id="strict_geocoding" if variant == "strict" else "ranked_capital",
                version="1",
                strategy="strict" if variant == "strict" else "ranked",
                prefer_prominent=variant != "strict",
                auto_accept_ranked=variant != "strict",
            )
        }
    else:
        providers.register(
            capability_id="resolve_entity",
            provider_id=source_id,
            handler=WikidataMountainResolver(transport),
        )
        providers.register(
            capability_id="lookup_property",
            provider_id=source_id,
            handler=WikidataElevationHandler(transport),
        )
        resolution_services = {
            "resolve_entity": WikidataMountainResolutionService(transport)
        }
        resolution_policies = {
            "resolve_entity": ResolutionPolicy(
                policy_id="strict_mountains", version="1", strategy="strict"
            )
        }

    job = JobExecutor(
        handlers=providers.resolve_handlers(
            capability_ids=sorted(binding), selected_providers=binding
        ),
        selected_providers=binding,
        configuration_ids={cap: "m2-benchmark-v1" for cap in binding},
        capability_versions={spec.id: spec.version for spec in get_builtin_capabilities()},
        provider_versions={cap: "offline-v1" for cap in binding},
        policy=JobExecutionPolicy(
            max_rows=1000,
            batch_size=100,
            max_memoized_results=64,
            memoizable_providers=frozenset(binding.items()),
        ),
        request_session=session,
        resolution_services=resolution_services,
        resolution_policies=resolution_policies,
    )
    started = perf_counter()
    result = job.execute(plan, records, job_id=f"m2-{scenario}-{variant}-{rows}")
    elapsed = perf_counter() - started

    if result.resolution_report is None:
        raise AssertionError("Audited execution must return the complete resolution report.")
    audits = result.resolution_report.operations
    link_count = sum(len(a.record_links) for a in audits)
    decision_counts: dict[str, int] = {}
    for audit in audits:
        name = audit.report.decision.status
        decision_counts[name] = decision_counts.get(name, 0) + 1
    evidence_count = sum(
        len(step.result.evidence)
        for record in result.records
        for step in record.row_result.steps
    )
    counters = result.provider_requests[source_id]

    # These are acceptance invariants, NOT performance-duration thresholds.
    assert result.metrics.total_rows == rows
    expected_logical = (
        (3 if scenario == "capitals" else 2) * rows
        if variant == "normal"
        else 2
        if variant == "strict"
        else 1
    )
    assert result.metrics.logical_operations == expected_logical
    assert result.metrics.logical_operations == (
        result.metrics.executed_handler_calls
        + result.metrics.reused_operations
        + result.metrics.persistent_cache_hits
    )
    assert counters.transport_attempts == fixture.attempts
    assert counters.requests_blocked_by_budget == 0

    if variant == "normal":
        assert result.metrics.successful_rows == rows
        assert result.metrics.failed_rows == 0
        assert result.metrics.partial_rows == 0
        assert result.resolution_report.status == "completed"
        assert link_count == (2 if scenario == "capitals" else 1) * rows
        assert evidence_count >= (3 if scenario == "capitals" else 2) * rows
        for record in result.records:
            last = record.row_result.steps[-1].result
            assert last.status == "success"
            assert isinstance(last.value, Quantity)
            assert last.value.unit == ("km" if scenario == "capitals" else "m")
            if scenario == "capitals":
                assert last.value.value > 0
            else:
                assert last.value.value == 8848.86
        if scenario == "capitals":
            assert result.metrics.executed_handler_calls == (3 if rows == 1 else 4)
            assert counters.transport_attempts == 2
            assert len(audits) == 2
            berlin = [
                audit for audit in audits
                if audit.report.candidate_set.query.name == "Berlin"
            ]
            assert len(berlin) == 1
            assert berlin[0].report.decision.status == "auto_resolved"
            assert berlin[0].report.decision.selected is not None
            assert berlin[0].report.decision.selected.entity_id == "2950159"
            assert len(berlin[0].report.candidate_set.candidates) == 2
        else:
            assert result.metrics.executed_handler_calls == 2
            assert counters.transport_attempts == 5
            assert len(audits) == 1
            assert audits[0].report.decision.selected == EntityRef(
                source="wikidata", entity_id="Q513"
            )
            assert len(audits[0].report.candidate_set.candidates) == 2
            assert any(
                c.verification == "rejected"
                for c in audits[0].report.candidate_set.candidates
            )
    elif variant == "strict":
        assert rows == 1
        assert result.metrics.partial_rows == 1
        assert counters.transport_attempts == 2
        assert result.resolution_report.status == "awaiting_review"
        assert decision_counts.get("needs_review") == 1
        assert result.records[0].row_result.steps[-1].result.error_code == "DEPENDENCY_FAILED"
        assert link_count == 2
    else:
        assert rows == 1
        assert result.metrics.failed_rows == 1
        assert result.resolution_report.status == "awaiting_review"
        assert decision_counts.get("incomplete_verification") == 1
        assert result.records[0].row_result.steps[-1].result.error_code == "DEPENDENCY_FAILED"
        assert audits[0].report.decision.selected is None
        assert any(
            c.verification == "unchecked"
            for c in audits[0].report.candidate_set.candidates
        )

    return BenchmarkResult(
        scenario=scenario,
        variant=variant,
        rows=rows,
        elapsed_seconds=elapsed,
        logical_operations=result.metrics.logical_operations,
        executed_handler_calls=result.metrics.executed_handler_calls,
        reused_operations=result.metrics.reused_operations,
        admitted_transport_attempts=counters.transport_attempts,
        fixture_transport_calls=fixture.attempts,
        successful_rows=result.metrics.successful_rows,
        partial_rows=result.metrics.partial_rows,
        failed_rows=result.metrics.failed_rows,
        audit_operations=len(audits),
        audit_row_step_links=link_count,
        resolution_decisions=decision_counts,
        evidence_count=evidence_count,
    )


def run_acceptance(rows: int = 1000) -> dict[str, object]:
    """Run both mandatory scenarios plus explicit abstention/dependency cases."""
    benchmarks = [
        run_scenario("capitals", rows=rows),
        run_scenario("mountains", rows=rows),
        run_scenario("capitals", rows=1, variant="strict"),
        run_scenario("mountains", rows=1, variant="unchecked_mountain"),
    ]
    return {
        "kind": "m2-controlled-offline-acceptance",
        "network": "disabled; transport is synthetic",
        "timing_scope": "JobExecutor.execute only; excludes plan validation and fixture setup",
        "timing_note": (
            "Measured elapsed seconds on current host; not a provider network latency SLA"
        ),
        "benchmarks": [result.as_dict() for result in benchmarks],
    }
