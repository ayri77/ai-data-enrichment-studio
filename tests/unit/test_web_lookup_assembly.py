"""The executor assembly for the web lookup fallback: errors, budget, memo, kept answers."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tests.support.web_lookup import (
    FakeApi,
    RowApi,
    StatusError,
    empty_reply,
    good_reply,
    response,
)

from ai_data_enrichment.capabilities.bindings import BINDINGS, FALLBACK_BINDINGS
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
from ai_data_enrichment.execution.models import FoundRecord
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.orchestration.live_executor import (
    LIVE_SOURCES,
    WebLookupOptions,
    build_live_executor,
)
from ai_data_enrichment.providers import web_lookup as web_lookup_module
from ai_data_enrichment.providers.request_policy import ProviderRequestPolicy
from ai_data_enrichment.providers.web_lookup import WebLookupUsage
from ai_data_enrichment.resolution.sqlite_cache import SQLiteResolutionCache
from ai_data_enrichment.run_limits import RESULT_REUSE_SECONDS

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


def kept_run(
    tmp_path: Path,
    api: Any,
    usage: WebLookupUsage,
    *mountains: str,
    plan: Any = PLAN,
    columns: Any = COLUMNS,
    model: str = "m",
    effort: str | None = None,
    clock: Any = None,
    max_rows: int = 5,
    records: list[InputRecord] | None = None,
) -> JobExecutionResult:
    """One job of a plan whose caches are the files in ``tmp_path``."""
    extra = {"clock": clock} if clock is not None else {}
    with (
        SQLiteOperationCache(tmp_path / "values.sqlite", **extra) as values,
        SQLiteResolutionCache(tmp_path / "entities.sqlite") as entities,
    ):
        executor = build_live_executor(
            fallback_registry(plan, columns),
            max_rows=max_rows,
            web_lookup=WebLookupOptions(api=api, model=model, reasoning_effort=effort, usage=usage),
            reuse=(values, entities),
        )
        return executor.execute(plan, records if records is not None else rows(*mountains))


def test_identical_rows_cost_one_request_and_the_next_job_costs_none(tmp_path: Path) -> None:
    api, usage = good_api(), WebLookupUsage()
    result = kept_run(tmp_path, api, usage, "Everest", "Everest", "Lhotse", "Everest")
    assert len(api.calls) == usage.totals().requests == 2
    metrics = result.metrics
    assert (metrics.logical_operations, metrics.executed_handler_calls) == (4, 2)
    assert (metrics.reused_operations, metrics.memoized_results) == (2, 2)
    # Both fresh answers were written; nothing came from an earlier job.
    assert metrics.persistent_cache_writes == 2 and metrics.persistent_cache_hits == 0
    assert result.kept_answers == {}
    assert result.provider_requests[SOURCE].operations_started == 2  # a memo hit costs no budget

    again_api, again_usage = good_api(), WebLookupUsage()
    again = kept_run(tmp_path, again_api, again_usage, "Everest", "Everest", "Lhotse", "Everest")
    assert again_api.calls == [] and again_usage.totals().requests == 0
    metrics = again.metrics
    # Two answers read from the file; the repeats of Everest reuse the one in memory, and
    # that is still a kept answer: all four rows are settled by what the first job kept.
    assert (metrics.executed_handler_calls, metrics.persistent_cache_hits) == (0, 2)
    assert (metrics.reused_operations, metrics.persistent_cache_writes) == (2, 0)
    assert again.kept_answers == {"web_lookup": 4}
    # A kept answer starts no request and spends none of the job's budget.
    assert again.provider_requests[SOURCE].operations_started == 0
    assert [r.row_result for r in again.records] == [r.row_result for r in result.records]


def test_the_lifetime_of_a_kept_answer_is_the_one_of_the_other_capabilities() -> None:
    executor = build_live_executor(fallback_registry(PLAN, COLUMNS), web_lookup=options(good_api()))
    assert executor._policy.persistent_cache_ttls == {}  # pyright: ignore[reportPrivateUsage]
    with (
        SQLiteOperationCache(":memory:") as values,
        SQLiteResolutionCache(":memory:") as entities,
    ):
        executor = build_live_executor(
            fallback_registry(PLAN, COLUMNS),
            web_lookup=options(good_api()),
            reuse=(values, entities),
        )
        ttls = executor._policy.persistent_cache_ttls  # pyright: ignore[reportPrivateUsage]
    assert (
        ttls[("web_lookup", SOURCE)]
        == RESULT_REUSE_SECONDS
        == ttls[("lookup_property", "wikidata")]
    )
    assert "web_lookup" not in {binding.capability_id for binding in BINDINGS}
    assert FALLBACK_BINDINGS[0].reusable


def test_a_kept_answer_expires_with_the_lifetime(tmp_path: Path) -> None:
    now = [1_000.0]
    api, usage = good_api(), WebLookupUsage()
    kept_run(tmp_path, api, usage, "Everest", clock=lambda: now[0])
    assert len(api.calls) == 1
    now[0] += RESULT_REUSE_SECONDS - 1
    kept_run(tmp_path, api, usage, "Everest", clock=lambda: now[0])
    assert len(api.calls) == 1  # still kept
    now[0] += 1
    again = kept_run(tmp_path, api, usage, "Everest", clock=lambda: now[0])
    assert len(api.calls) == 2  # gone: asked again, and kept again
    assert again.metrics.persistent_cache_writes == 1


@pytest.mark.parametrize("code", ["NOTHING_FOUND", "INVALID_RESPONSE", "PROVIDER_ERROR"])
def test_a_failed_row_is_not_kept_and_is_asked_again(tmp_path: Path, code: str) -> None:
    failing: dict[str, Any] = {
        "NOTHING_FOUND": response(empty_reply()),
        "INVALID_RESPONSE": response("not json"),
        "PROVIDER_ERROR": StatusError(500),
    }
    first_api = FakeApi(failing[code])
    first = kept_run(tmp_path, first_api, WebLookupUsage(), "Everest", "Lhotse")
    assert first.records[0].row_result.steps[0].result.error_code is not None
    assert first.metrics.persistent_cache_writes == 0
    second_api = good_api()
    second = kept_run(tmp_path, second_api, WebLookupUsage(), "Everest", "Lhotse")
    assert len(second_api.calls) == 2  # nothing was kept for either row
    assert second.metrics.persistent_cache_hits == 0 and second.kept_answers == {}
    assert second.metrics.successful_rows == 2


def test_rows_after_the_deadline_are_asked_by_the_next_job_and_answered_rows_are_not(
    tmp_path: Path,
) -> None:
    ticks = iter(range(100))
    api, usage = good_api(), WebLookupUsage()
    with (
        SQLiteOperationCache(tmp_path / "values.sqlite") as values,
        SQLiteResolutionCache(tmp_path / "entities.sqlite") as entities,
    ):
        # The clock moves six seconds per look and a request may take 30, so only a request
        # begun at second 5 or earlier fits before second 35: the first row's, no later one.
        first = build_live_executor(
            fallback_registry(PLAN, COLUMNS),
            max_rows=5,
            web_lookup=options(
                api,
                usage,
                deadline=35.0,
                timeout_seconds=30.0,
                clock=lambda: float(next(ticks)) * 6,
            ),
            reuse=(values, entities),
        ).execute(PLAN, rows("Everest", "Lhotse", "Makalu"))
    codes = [r.row_result.steps[0].result.error_code for r in first.records]
    assert codes == [None, "DEADLINE_REACHED", "DEADLINE_REACHED"]
    assert len(api.calls) == 1

    second_api, second_usage = good_api(), WebLookupUsage()
    second = kept_run(tmp_path, second_api, second_usage, "Everest", "Lhotse", "Makalu")
    assert len(second_api.calls) == second_usage.totals().requests == 2  # not Everest
    assert all("Everest" not in call["input"] for call in second_api.calls)
    assert second.kept_answers == {"web_lookup": 1} and second.metrics.successful_rows == 3
    assert second.provider_requests[SOURCE].operations_started == 2


def test_a_kept_answer_is_served_after_the_deadline_without_a_request(tmp_path: Path) -> None:
    kept_run(tmp_path, good_api(), WebLookupUsage(), "Everest")
    api = good_api()
    with (
        SQLiteOperationCache(tmp_path / "values.sqlite") as values,
        SQLiteResolutionCache(tmp_path / "entities.sqlite") as entities,
    ):
        late = build_live_executor(
            fallback_registry(PLAN, COLUMNS),
            web_lookup=options(api, deadline=0.0, clock=lambda: 10.0),
            reuse=(values, entities),
        ).execute(PLAN, rows("Everest", "Lhotse"))
    codes = [r.row_result.steps[0].result.error_code for r in late.records]
    assert codes == [None, "DEADLINE_REACHED"] and api.calls == []


def _changed(request: FallbackRequest) -> Any:
    plan = build_fallback_plan(request, COLUMNS)
    return plan


QUESTION_CHANGED = _changed(
    FallbackRequest(
        question="Find other facts about this mountain.",
        context_columns=REQUEST.context_columns,
        targets=REQUEST.targets,
    )
)
CONTEXT_CHANGED = _changed(
    FallbackRequest(
        question=REQUEST.question, context_columns=("Mountain",), targets=REQUEST.targets
    )
)
TARGET_KIND_CHANGED = _changed(
    FallbackRequest(
        question=REQUEST.question,
        context_columns=REQUEST.context_columns,
        targets=(
            FallbackTarget(column="Height", kind="text"),
            REQUEST.targets[1],
            REQUEST.targets[2],
        ),
    )
)
TARGET_UNIT_CHANGED = _changed(
    FallbackRequest(
        question=REQUEST.question,
        context_columns=REQUEST.context_columns,
        targets=(
            FallbackTarget(column="Height", kind="number", unit="ft"),
            REQUEST.targets[1],
            REQUEST.targets[2],
        ),
    )
)
TARGET_COLUMN_CHANGED = _changed(
    FallbackRequest(
        question=REQUEST.question,
        context_columns=REQUEST.context_columns,
        targets=(
            FallbackTarget(column="Altitude", kind="number", unit="m"),
            REQUEST.targets[1],
            REQUEST.targets[2],
        ),
    )
)


@pytest.mark.parametrize(
    "plan",
    [
        QUESTION_CHANGED,
        CONTEXT_CHANGED,
        TARGET_KIND_CHANGED,
        TARGET_UNIT_CHANGED,
        TARGET_COLUMN_CHANGED,
    ],
    ids=["question", "context columns", "target kind", "target unit", "target column"],
)
def test_another_question_or_other_columns_do_not_reuse_the_answer(
    tmp_path: Path, plan: Any
) -> None:
    kept_run(tmp_path, good_api(), WebLookupUsage(), "Everest")
    columns = [
        *COLUMNS,
        ColumnProfile(name="Altitude", inferred_type=PrimitiveType.TEXT, null_count=0),
    ]
    # The plans differ in their task text, which is an input of the operation.
    assert plan.steps[0].inputs["task"] != PLAN.steps[0].inputs["task"]
    api, usage = good_api(), WebLookupUsage()
    result = kept_run(tmp_path, api, usage, "Everest", plan=plan, columns=columns)
    assert len(api.calls) == usage.totals().requests == 1
    assert result.kept_answers == {} and result.metrics.persistent_cache_hits == 0


def test_other_row_values_do_not_reuse_the_answer(tmp_path: Path) -> None:
    kept_run(tmp_path, good_api(), WebLookupUsage(), "Everest")
    api = good_api()
    other_country = [
        InputRecord(record_id="r1", values={"Mountain": "Everest", "Country": "China"})
    ]
    result = kept_run(tmp_path, api, WebLookupUsage(), records=other_country)
    assert len(api.calls) == 1 and result.kept_answers == {}
    # The same two values but a case apart are two rows for the model as well.
    api = good_api()
    kept_run(tmp_path, api, WebLookupUsage(), "everest")
    assert len(api.calls) == 1


@pytest.mark.parametrize(
    ("model", "effort"), [("other-model", None), ("m", "high")], ids=["model", "effort"]
)
def test_another_model_or_effort_does_not_reuse_the_answer(
    tmp_path: Path, model: str, effort: str | None
) -> None:
    kept_run(tmp_path, good_api(), WebLookupUsage(), "Everest")
    api = good_api()
    result = kept_run(tmp_path, api, WebLookupUsage(), "Everest", model=model, effort=effort)
    assert len(api.calls) == 1 and result.kept_answers == {}


def test_another_prompt_version_does_not_reuse_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept_run(tmp_path, good_api(), WebLookupUsage(), "Everest")
    monkeypatch.setattr(web_lookup_module, "PROMPT_VERSION", "2")
    api = good_api()
    result = kept_run(tmp_path, api, WebLookupUsage(), "Everest")
    assert len(api.calls) == 1 and result.kept_answers == {}


def test_what_is_kept_is_what_was_found_with_its_evidence(tmp_path: Path) -> None:
    first = kept_run(tmp_path, good_api(), WebLookupUsage(), "Everest")
    api = good_api()
    again = kept_run(tmp_path, api, WebLookupUsage(), "Everest")
    assert api.calls == []
    before, after = first.records[0].row_result.steps[0], again.records[0].row_result.steps[0]
    assert after == before
    assert isinstance(after.result.value, FoundRecord) and after.result.evidence


def test_the_file_holds_no_key_and_nothing_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level("DEBUG")
    secret = "sk-test-secret"
    api = RowApi(lambda kwargs: response(good_reply()), api_key=secret)
    kept_run(tmp_path, api, WebLookupUsage(), "Everest")
    kept_run(tmp_path, api, WebLookupUsage(), "Everest")
    assert len(api.calls) == 1
    database = sqlite3.connect(tmp_path / "values.sqlite")
    try:
        stored = [
            str(value) for row in database.execute("select * from operation_cache") for value in row
        ]
    finally:
        database.close()
    assert len(stored) == 7  # one entry
    text = "\n".join(stored) + (tmp_path / "values.sqlite").read_bytes().decode("latin-1")
    assert secret not in text
    # The key of the entry is a digest: neither the question nor the row is in it.
    assert "Everest" not in stored[0] and "mountain" not in stored[0]
    out, err = capfd.readouterr()
    assert not caplog.text and "Everest" not in out + err


def test_a_corrupt_kept_answer_is_dropped_and_asked_again(tmp_path: Path) -> None:
    kept_run(tmp_path, good_api(), WebLookupUsage(), "Everest")
    database = sqlite3.connect(tmp_path / "values.sqlite")
    database.execute('update operation_cache set result_json = \'{"format": "x"}\'')
    database.commit()
    database.close()
    api = good_api()
    result = kept_run(tmp_path, api, WebLookupUsage(), "Everest")
    assert len(api.calls) == 1 and result.metrics.persistent_cache_invalid_entries == 1
    assert result.metrics.persistent_cache_writes == 1


def test_a_plan_without_the_lookup_is_assembled_as_before(tmp_path: Path) -> None:
    registry = CapabilityRegistry()
    for spec in offered_capabilities(road_distance=False):
        registry.register(spec)
    with (
        SQLiteOperationCache(tmp_path / "values.sqlite") as values,
        SQLiteResolutionCache(tmp_path / "entities.sqlite") as entities,
    ):
        executor = build_live_executor(registry, reuse=(values, entities))
        ttls = executor._policy.persistent_cache_ttls  # pyright: ignore[reportPrivateUsage]
    assert set(ttls) == {("lookup_property", "wikidata")}


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
