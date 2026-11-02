"""Reproducible M2 smoke runs using validated plans and the real JobExecutor.

Offline is always the default. Live traffic requires an explicit CLI opt-in.
This is a developer diagnostic, not a workbook enrichment or public CLI feature.
"""

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Literal

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionPolicy
from ai_data_enrichment.execution.models import RuntimeValue
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.evaluation.source_trace import SourceTraceTransport
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.http_transport import JsonTransport, UrllibJsonTransport
from ai_data_enrichment.providers.open_meteo import GEOCODING_URL, OpenMeteoGeocodingHandler
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
from ai_data_enrichment.validation.plan_validator import PlanValidator

Scenario = Literal["capitals", "mountains"]
Mode = Literal["offline", "live", "cache"]
_AGENT = "AIDataEnrichmentStudio/0.1 (https://github.com/ayri77/ai-data-enrichment-studio)"


def _statement(qid: str) -> dict[str, object]:
    return {
        "rank": "normal",
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {"type": "wikibase-entityid", "value": {"id": qid}},
        },
    }


class OfflineGeocodeTransport:
    """Small, deterministic contract fixture exercising the real geocoder."""

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        if url != GEOCODING_URL:
            raise ValueError("Unexpected offline endpoint.")
        city = params["name"]
        places = {
            "Hamburg": (2911298, 53.55073, 9.99302),
            "Berlin": (2950159, 52.52437, 13.41053),
        }
        item = places.get(city)
        if item is None:
            return {"results": []}
        identifier, latitude, longitude = item
        return {
            "results": [
                {
                    "id": identifier,
                    "name": city,
                    "country_code": "DE",
                    "latitude": latitude,
                    "longitude": longitude,
                }
            ]
        }


class OfflineWikidataTransport:
    """Four-response Everest fixture; no HTTP or unpredictable external data."""

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        if url != WIKIDATA_API_URL:
            raise ValueError("Unexpected offline endpoint.")
        if params["action"] == "wbsearchentities":
            if params["search"] == "Mount Everest":
                return {"search": [{"id": "Q513", "label": "Mount Everest"}]}
            return {"search": []}
        if params["action"] != "wbgetentities":
            raise ValueError("Unsupported offline Wikidata action.")
        if params["ids"] == "Q513":
            return {
                "entities": {
                    "Q513": {
                        "id": "Q513",
                        "claims": {
                            "P31": [_statement("Q8502")],
                            "P17": [_statement("Q837")],
                            "P2044": [
                                {
                                    "rank": "normal",
                                    "mainsnak": {
                                        "snaktype": "value",
                                        "datavalue": {
                                            "type": "quantity",
                                            "value": {
                                                "amount": "+8848.86",
                                                "unit": "http://www.wikidata.org/entity/Q11573",
                                            },
                                        },
                                    },
                                }
                            ],
                        },
                    }
                }
            }
        if params["ids"] == "Q837":
            return {
                "entities": {
                    "Q837": {
                        "id": "Q837",
                        "claims": {
                            "P297": [
                                {
                                    "rank": "normal",
                                    "mainsnak": {
                                        "snaktype": "value",
                                        "datavalue": {"type": "string", "value": "NP"},
                                    },
                                }
                            ]
                        },
                    }
                }
            }
        raise ValueError("Unexpected offline Wikidata entity.")


def _plan(scenario: Scenario) -> EnrichmentPlan:
    if scenario == "mountains":
        return EnrichmentPlan(
            version="1.0",
            steps=[
                PlanStep(
                    id="resolve",
                    capability="resolve_entity",
                    inputs={
                        "name": ColumnReference(kind="column", name="mountain"),
                        "country": ColumnReference(kind="column", name="country"),
                    },
                    parameters={"entity_type": "mountain"},
                ),
                PlanStep(
                    id="elevation",
                    capability="lookup_property",
                    inputs={"entity": StepOutputReference(kind="step_output", step_id="resolve")},
                    parameters={"property": "elevation"},
                ),
            ],
            outputs=[
                OutputMapping(step_id="elevation", value_field="value", target_column="elevation_m")
            ],
            assumptions=[],
        )

    return EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="a",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="city_a"),
                    "country": ColumnReference(kind="column", name="country_a"),
                },
                parameters={},
            ),
            PlanStep(
                id="b",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="city_b"),
                    "country": ColumnReference(kind="column", name="country_b"),
                },
                parameters={},
            ),
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(kind="step_output", step_id="a"),
                    "point_b": StepOutputReference(kind="step_output", step_id="b"),
                },
                parameters={"unit": "km"},
            ),
        ],
        outputs=[
            OutputMapping(step_id="distance", value_field="value", target_column="distance_km")
        ],
        assumptions=[],
    )


def _inputs(scenario: Scenario, *, live: bool) -> list[InputRecord]:
    values: dict[str, RuntimeValue | None] = (
        {"mountain": "Mount Everest", "country": "Nepal"}
        if scenario == "mountains"
        else {
            "city_a": "Hamburg",
            "country_a": "Germany",
            "city_b": "Berlin",
            "country_b": "Germany",
        }
    )
    # Repetition in offline/cache modes proves in-job deduplication.
    return [
        InputRecord(record_id=f"row-{index + 1}", values=values)
        for index in range(1 if live else 2)
    ]


def _validate(plan: EnrichmentPlan, records: list[InputRecord]) -> CapabilityRegistry:
    capabilities = CapabilityRegistry()
    for spec in get_builtin_capabilities():
        capabilities.register(spec)
    profile = DatasetProfile(
        dataset_id="smoke-fixture",
        name="M2 controlled smoke input",
        columns=[
            ColumnProfile(name=column, inferred_type=PrimitiveType.TEXT, null_count=0)
            for column in records[0].values
        ],
    )
    validation = PlanValidator(registry=capabilities, dataset_profile=profile).validate(plan)
    if not validation.valid:
        raise ValueError(f"Smoke plan is invalid: {validation.errors}")
    return capabilities


def _execute(
    scenario: Scenario,
    *,
    live: bool,
    cache: SQLiteOperationCache | None,
    diagnose: bool = False,
) -> dict[str, object]:
    records = _inputs(scenario, live=live)
    plan = _plan(scenario)
    capabilities = _validate(plan, records)
    providers = ProviderRegistry(capabilities)
    selected: dict[str, str]
    source_id = "wikidata" if scenario == "mountains" else "open-meteo"
    selected = (
        {"resolve_entity": source_id, "lookup_property": source_id}
        if scenario == "mountains"
        else {"geocode": source_id, "geodesic_distance": "local-haversine"}
    )
    backend: JsonTransport = (
        UrllibJsonTransport(headers={"User-Agent": _AGENT})
        if live
        else OfflineWikidataTransport()
        if scenario == "mountains"
        else OfflineGeocodeTransport()
    )
    trace = SourceTraceTransport(backend) if diagnose else None
    if trace is not None:
        backend = trace
    session = ProviderRequestSession(
        {
            source_id: ProviderRequestPolicy(
                max_requests_per_job=16 if scenario == "mountains" else 4,
                min_interval_seconds=0.75 if live else 0.0,
                max_total_wait_seconds=30.0,
            )
        }
    )
    bounded_transport = session.wrap_transport(
        source_id, backend, retry_policy=TransportRetryPolicy(max_attempts=1)
    )
    if scenario == "mountains":
        providers.register(
            capability_id="resolve_entity",
            provider_id=source_id,
            handler=WikidataMountainResolver(bounded_transport),
        )
        providers.register(
            capability_id="lookup_property",
            provider_id=source_id,
            handler=WikidataElevationHandler(bounded_transport),
        )
    else:
        providers.register(
            capability_id="geocode",
            provider_id=source_id,
            handler=OpenMeteoGeocodingHandler(bounded_transport),
        )
        providers.register(
            capability_id="geodesic_distance",
            provider_id="local-haversine",
            handler=GeodesicDistanceHandler(),
        )

    capability_ids = sorted(selected)
    ttl = {
        (capability_id, provider_id): 3600.0
        for capability_id, provider_id in selected.items()
        if provider_id == source_id and cache is not None
    }
    policy = JobExecutionPolicy(
        max_rows=2,
        batch_size=1,
        max_memoized_results=16,
        memoizable_providers=frozenset(
            (capability_id, provider_id) for capability_id, provider_id in selected.items()
        ),
        persistent_cache_ttls=ttl,
    )
    engine = JobExecutor(
        handlers=providers.resolve_handlers(
            capability_ids=capability_ids, selected_providers=selected
        ),
        selected_providers=selected,
        configuration_ids={cap: "m2-smoke-v1" for cap in capability_ids},
        capability_versions={spec.id: spec.version for spec in get_builtin_capabilities()},
        provider_versions={cap: "m2-smoke-v1" for cap in capability_ids},
        policy=policy,
        persistent_cache=cache,
        request_session=session,
    )
    started = perf_counter()
    job = engine.execute(plan, records)
    elapsed = perf_counter() - started
    return {
        **({"source_trace": trace.events} if trace is not None else {}),
        "duration_seconds": round(elapsed, 4),
        "metrics": job.metrics.model_dump(mode="json"),
        "requests": {provider: vars(stats) for provider, stats in job.provider_requests.items()},
        "records": [
            {
                "id": record.record_id,
                "steps": [
                    {"id": step.step_id, **step.result.model_dump(mode="json")}
                    for step in record.row_result.steps
                ],
            }
            for record in job.records
        ],
    }


def run_smoke(
    scenario: Scenario,
    mode: Mode = "offline",
    *,
    allow_network: bool = False,
    diagnose: bool = False,
) -> dict[str, object]:
    """Run exact source handlers; live mode cannot be activated accidentally."""
    if diagnose and mode != "live":
        raise ValueError("Source diagnosis is only available in live mode.")
    if mode == "live":
        if not allow_network:
            raise ValueError("Live mode requires --allow-network.")
        return {
            "mode": mode,
            "scenario": scenario,
            "runs": [_execute(scenario, live=True, cache=None, diagnose=diagnose)],
        }
    if mode == "offline":
        return {
            "mode": mode,
            "scenario": scenario,
            "runs": [_execute(scenario, live=False, cache=None)],
        }
    if mode == "cache":
        with TemporaryDirectory(prefix="ai-enrichment-smoke-") as directory:
            path = Path(directory) / "operations.sqlite3"
            with SQLiteOperationCache(path) as cache:
                first = _execute(scenario, live=False, cache=cache)
            with SQLiteOperationCache(path) as cache:
                second = _execute(scenario, live=False, cache=cache)
        return {"mode": mode, "scenario": scenario, "runs": [first, second]}
    raise ValueError(f"Unknown smoke mode: {mode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="M2 smoke tests (no workbook writes)")
    parser.add_argument("--scenario", choices=("capitals", "mountains"), required=True)
    parser.add_argument("--mode", choices=("offline", "live", "cache"), default="offline")
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="Explicitly permit bounded outbound API requests in --mode live.",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Show sanitized public-source candidates and Wikidata class paths in live mode.",
    )
    args = parser.parse_args()
    if args.mode != "live" and args.allow_network:
        parser.error("--allow-network is only valid with --mode live.")
    if args.mode == "live" and not args.allow_network:
        parser.error("--mode live requires --allow-network.")
    if args.diagnose and args.mode != "live":
        parser.error("--diagnose requires --mode live.")
    scenario: Scenario = args.scenario
    mode: Mode = args.mode
    report = run_smoke(
        scenario, mode, allow_network=args.allow_network, diagnose=args.diagnose
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    runs = report["runs"]
    assert isinstance(runs, list)
    return (
        0
        if all(
            item["metrics"]["failed_rows"] == 0 and item["metrics"]["partial_rows"] == 0
            for item in runs
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
