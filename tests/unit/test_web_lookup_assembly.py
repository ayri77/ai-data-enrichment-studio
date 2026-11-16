"""The executor assembly for the web lookup fallback: errors, budget, memo, no reuse."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tests.support.web_lookup import FakeApi, empty_reply, good_reply, response

from ai_data_enrichment.capabilities.bindings import BINDINGS
from ai_data_enrichment.capabilities.definitions import offered_capabilities
from ai_data_enrichment.capabilities.fallback import (
    FallbackRequest,
    FallbackTarget,
    build_fallback_plan,
    fallback_registry,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import ColumnProfile, PrimitiveType
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionResult
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.orchestration.live_executor import (
    LIVE_SOURCES,
    WebLookupOptions,
    build_live_executor,
)
from ai_data_enrichment.providers.request_policy import ProviderRequestPolicy
from ai_data_enrichment.providers.web_lookup import WebLookupUsage
from ai_data_enrichment.resolution.sqlite_cache import SQLiteResolutionCache

COLUMNS = [
    ColumnProfile(name=name, inferred_type=PrimitiveType.TEXT, null_count=0)
    for name in ("Mountain", "Country")
]
REQUEST = FallbackRequest(
    question="Find facts about this mountain.",
    context_columns=("Mountain", "Country"),
    targets=(
        FallbackTarget(column="Height", kind="number", unit="m"),
        FallbackTarget(column="First ascent", kind="text"),
        FallbackTarget(column="Climbed in winter", kind="yes_no"),
    ),
)
PLAN = build_fallback_plan(REQUEST, COLUMNS)
SOURCE = "openai_web_search"


def options(api: Any, usage: WebLookupUsage | None = None, **extra: Any) -> WebLookupOptions:
    return WebLookupOptions(
        api=api, model="m", reasoning_effort=None, usage=usage or WebLookupUsage(), **extra
    )


def rows(*mountains: str) -> list[InputRecord]:
    return [
        InputRecord(record_id=f"r{index}", values={"Mountain": name, "Country": "Nepal"})
        for index, name in enumerate(mountains, start=1)
    ]


def good_api() -> FakeApi:
    return FakeApi(response(good_reply()))


def run(executor: Any, *mountains: str) -> JobExecutionResult:
    return executor.execute(PLAN, rows(*mountains))


def test_a_registry_with_web_lookup_cannot_be_assembled_without_the_options() -> None:
    with pytest.raises(ValueError, match="needs the web lookup options"):
        build_live_executor(fallback_registry(PLAN, COLUMNS))


def test_options_without_web_lookup_in_the_registry_bind_nothing() -> None:
    api = good_api()
    registry = CapabilityRegistry()
    for spec in offered_capabilities(road_distance=False):
        registry.register(spec)
    executor = build_live_executor(registry, web_lookup=options(api))
    with pytest.raises(ValueError, match="Incomplete job provider bindings"):
        run(executor, "Everest")
    assert api.calls == []


def test_a_registry_without_web_lookup_is_assembled_as_before() -> None:
    registry = CapabilityRegistry()
    for spec in offered_capabilities(road_distance=False):
        registry.register(spec)
    executor = build_live_executor(registry)
    assert executor._request_session is None  # pyright: ignore[reportPrivateUsage]
    assert "web_lookup" not in LIVE_SOURCES
    assert all(binding.capability_id != "web_lookup" for binding in BINDINGS)


def test_the_caller_cannot_set_the_limit_of_the_lookup() -> None:
    policy = ProviderRequestPolicy(max_requests_per_job=1, max_operations_per_job=1)
    with pytest.raises(ValueError, match="set by the assembly"):
        build_live_executor(
            fallback_registry(PLAN, COLUMNS),
            web_lookup=options(good_api()),
            request_policies={SOURCE: policy},
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"model": " "}, "model"),
        ({"timeout_seconds": 0.0}, "timeout"),
        ({"timeout_seconds": -1.0}, "timeout"),
    ],
)
def test_bad_options_are_refused(changes: dict[str, Any], message: str) -> None:
    arguments: dict[str, Any] = {"model": "m", **changes}
    with pytest.raises(ValueError, match=message):
        WebLookupOptions(api=good_api(), reasoning_effort=None, usage=WebLookupUsage(), **arguments)


def test_more_rows_than_the_limit_are_refused_before_any_request() -> None:
    api = good_api()
    executor = build_live_executor(
        fallback_registry(PLAN, COLUMNS), max_rows=2, web_lookup=options(api)
    )
    with pytest.raises(ValueError, match="max_rows=2"):
        run(executor, "A", "B", "C")
    assert api.calls == []


def test_one_operation_per_row_is_the_budget_and_it_is_counted() -> None:
    api, usage = good_api(), WebLookupUsage()
    executor = build_live_executor(
        fallback_registry(PLAN, COLUMNS), max_rows=3, web_lookup=options(api, usage)
    )
    result = run(executor, "A", "B", "C")
    counters = result.provider_requests[SOURCE]
    assert (counters.operations_started, counters.operations_blocked_by_budget) == (3, 0)
    assert len(api.calls) == usage.totals().requests == 3
    assert result.metrics.executed_handler_calls == 3
    assert set(result.provider_requests) == {SOURCE}


def test_the_budget_stops_a_handler_call_beyond_the_rows_with_no_request() -> None:
    api = good_api()
    executor = build_live_executor(
        fallback_registry(PLAN, COLUMNS), max_rows=1, web_lookup=options(api)
    )
    session = executor._request_session  # pyright: ignore[reportPrivateUsage]
    assert session is not None
    handler = session.wrap_handler(SOURCE, executor._handlers["web_lookup"])  # pyright: ignore[reportPrivateUsage]
    inputs = {"task": PLAN.steps[0].inputs["task"].value, "ctx_1": "Everest"}  # type: ignore[union-attr]
    assert handler.execute(inputs=inputs, parameters={}).status == "success"
    refused = handler.execute(inputs=inputs, parameters={})
    assert refused.error_code == "PROVIDER_OPERATION_BUDGET_EXCEEDED"
    assert len(api.calls) == 1


def test_identical_rows_cost_one_request_and_are_not_stored_across_jobs(tmp_path: Path) -> None:
    api, usage = good_api(), WebLookupUsage()
    with (
        SQLiteOperationCache(tmp_path / "values.sqlite") as values,
        SQLiteResolutionCache(tmp_path / "entities.sqlite") as entities,
    ):
        executor = build_live_executor(
            fallback_registry(PLAN, COLUMNS),
            max_rows=5,
            web_lookup=options(api, usage),
            reuse=(values, entities),
        )
        result = run(executor, "Everest", "Everest", "Lhotse", "Everest")
    assert len(api.calls) == usage.totals().requests == 2
    metrics = result.metrics
    assert (metrics.logical_operations, metrics.executed_handler_calls) == (4, 2)
    assert (metrics.reused_operations, metrics.memoized_results) == (2, 2)
    assert metrics.persistent_cache_hits == metrics.persistent_cache_writes == 0
    assert metrics.persistent_cache_misses == 0
    assert result.provider_requests[SOURCE].operations_started == 2  # a memo hit costs no budget
    rows_in_files = 0
    for name in ("values.sqlite", "entities.sqlite"):
        database = sqlite3.connect(tmp_path / name)
        tables = [
            r[0] for r in database.execute("select name from sqlite_master where type='table'")
        ]
        rows_in_files += sum(
            database.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables
        )
        database.close()
    assert rows_in_files == 0


def test_without_reuse_no_sqlite_file_is_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    executor = build_live_executor(fallback_registry(PLAN, COLUMNS), web_lookup=options(good_api()))
    run(executor, "Everest", "Everest")
    assert [p.name for p in tmp_path.rglob("*") if "sqlite" in p.name or p.suffix == ".db"] == []


def test_a_failed_lookup_is_asked_again_for_the_identical_row() -> None:
    api = FakeApi(response(empty_reply()), response(good_reply()))
    usage = WebLookupUsage()
    executor = build_live_executor(
        fallback_registry(PLAN, COLUMNS), max_rows=3, web_lookup=options(api, usage)
    )
    result = run(executor, "Everest", "Everest", "Everest")
    codes = [r.row_result.steps[0].result.error_code for r in result.records]
    assert codes == ["NOTHING_FOUND", None, None]
    assert len(api.calls) == usage.totals().requests == 2


def test_the_lookup_budget_joins_the_policies_of_the_caller() -> None:
    api = good_api()
    other = ProviderRequestPolicy(max_requests_per_job=5, max_operations_per_job=5)
    executor = build_live_executor(
        fallback_registry(PLAN, COLUMNS),
        max_rows=2,
        web_lookup=options(api),
        request_policies={"wikidata": other, "open-meteo": other},
    )
    result = run(executor, "Everest")
    assert set(result.provider_requests) == {"wikidata", "open-meteo", SOURCE}
    assert result.provider_requests["wikidata"].operations_started == 0


def test_the_configuration_names_the_model_and_the_source_is_the_fallback_one() -> None:
    executor = build_live_executor(
        fallback_registry(PLAN, COLUMNS),
        web_lookup=WebLookupOptions(
            api=good_api(), model="gpt-x", reasoning_effort="low", usage=WebLookupUsage()
        ),
    )
    configs = executor._configuration_ids  # pyright: ignore[reportPrivateUsage]
    assert configs["web_lookup"] == "openai-web-lookup-p1-gpt-x-low"
    assert executor._selected_providers["web_lookup"] == SOURCE  # pyright: ignore[reportPrivateUsage]
