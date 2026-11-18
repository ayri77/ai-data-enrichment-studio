"""The executor assembly for the text extraction: refusal, budget, memo, kept answers."""

from typing import Any

import pytest
from tests.support.text_extraction import entry, response
from tests.support.web_lookup import FakeApi, RowApi

from ai_data_enrichment.capabilities.bindings import BINDINGS
from ai_data_enrichment.capabilities.definitions import offered_capabilities
from ai_data_enrichment.capabilities.extraction import (
    ExtractionRequest,
    ExtractionTarget,
    build_extraction_plan,
    extraction_registry,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import ColumnProfile, PrimitiveType
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionResult
from ai_data_enrichment.execution.models import FoundRecord
from ai_data_enrichment.orchestration.live_executor import (
    LIVE_SOURCES,
    ExtractionOptions,
    WebLookupOptions,
    build_live_executor,
)
from ai_data_enrichment.providers.request_policy import ProviderRequestPolicy
from ai_data_enrichment.providers.text_extraction import ExtractionUsage
from ai_data_enrichment.providers.web_lookup import WebLookupUsage
from ai_data_enrichment.run_limits import RESULT_REUSE_SECONDS

SOURCE = "openai_text_extraction"
COLUMNS = [
    ColumnProfile(name="Body", inferred_type=PrimitiveType.TEXT, null_count=0),
]
REQUEST = ExtractionRequest(
    question="Payments",
    text_column="Body",
    targets=(
        ExtractionTarget(column="Amount", kind="number"),
        ExtractionTarget(column="Service", kind="text"),
    ),
)
PLAN = build_extraction_plan(REQUEST, COLUMNS)
REGISTRY = extraction_registry(PLAN, COLUMNS)


def options(api: Any, usage: ExtractionUsage | None = None, **extra: Any) -> ExtractionOptions:
    return ExtractionOptions(
        api=api, model="m", reasoning_effort=None, usage=usage or ExtractionUsage(), **extra
    )


def rows(*bodies: str) -> list[InputRecord]:
    return [
        InputRecord(record_id=f"r{index}", values={"Body": body})
        for index, body in enumerate(bodies, start=1)
    ]


def good(quote: str = "Total: 9,99 EUR") -> Any:
    """A reply for the two slots of this task: the amount is said, the service is not."""
    return response(
        {"applies": True, "values": {"value_1": entry(9.99, quote), "value_2": entry()}}
    )


def not_applicable() -> Any:
    return response({"applies": False, "values": {"value_1": entry(), "value_2": entry()}})


def run(executor: Any, *bodies: str) -> JobExecutionResult:
    return executor.execute(PLAN, rows(*bodies))


def codes(result: JobExecutionResult) -> list[str | None]:
    return [r.row_result.steps[0].result.error_code for r in result.records]


def test_the_registry_of_an_extraction_plan_cannot_be_assembled_without_the_options() -> None:
    with pytest.raises(ValueError, match="needs the text extraction options"):
        build_live_executor(REGISTRY)
    # The web lookup's options are not the extraction's.
    with pytest.raises(ValueError, match="needs the text extraction options"):
        build_live_executor(
            REGISTRY,
            web_lookup=WebLookupOptions(
                api=FakeApi(), model="m", reasoning_effort=None, usage=WebLookupUsage()
            ),
        )


def test_options_without_the_capability_in_the_registry_bind_nothing() -> None:
    registry = CapabilityRegistry()
    for spec in offered_capabilities(road_distance=False):
        registry.register(spec)
    executor = build_live_executor(registry, text_extraction=options(FakeApi()))
    assert "text_extraction" not in executor._selected_providers  # pyright: ignore[reportPrivateUsage]
    assert executor._request_session is None  # pyright: ignore[reportPrivateUsage]


def test_it_is_bound_under_its_own_source_and_nothing_else_is() -> None:
    executor = build_live_executor(REGISTRY, text_extraction=options(FakeApi()))
    assert executor._selected_providers == {"text_extraction": SOURCE}  # pyright: ignore[reportPrivateUsage]
    assert "text_extraction" not in LIVE_SOURCES
    assert all(binding.capability_id != "text_extraction" for binding in BINDINGS)


def test_the_caller_cannot_set_the_limit_of_the_extraction() -> None:
    policy = ProviderRequestPolicy(max_requests_per_job=1, max_operations_per_job=1)
    with pytest.raises(ValueError, match="set by the assembly"):
        build_live_executor(
            REGISTRY, text_extraction=options(FakeApi()), request_policies={SOURCE: policy}
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
        ExtractionOptions(
            api=FakeApi(), reasoning_effort=None, usage=ExtractionUsage(), **arguments
        )


def test_more_rows_than_the_limit_are_refused_before_any_request() -> None:
    api = FakeApi(good())
    executor = build_live_executor(REGISTRY, max_rows=2, text_extraction=options(api))
    with pytest.raises(ValueError, match="max_rows=2"):
        run(executor, "a", "b", "c")
    assert api.calls == []


def test_one_operation_per_row_is_the_budget_and_it_is_counted() -> None:
    api, usage = FakeApi(good()), ExtractionUsage()
    executor = build_live_executor(REGISTRY, max_rows=5, text_extraction=options(api, usage))
    result = run(executor, "Total: 9,99 EUR", "Total: 9,99 EUR ", "Total: 9,99 EUR  ")
    assert len(api.calls) == usage.totals().requests == 3
    assert result.provider_requests[SOURCE].operations_started == 3
    assert result.metrics.reused_operations == 0


def test_the_same_text_is_asked_once_in_a_job_and_a_failure_is_not_kept() -> None:
    api = FakeApi(good())
    executor = build_live_executor(REGISTRY, text_extraction=options(api))
    result = run(executor, "Total: 9,99 EUR", "Total: 9,99 EUR")
    assert len(api.calls) == 1 and result.metrics.reused_operations == 1
    assert codes(result) == [None, None]

    api = FakeApi(not_applicable())
    executor = build_live_executor(REGISTRY, text_extraction=options(api))
    result = run(executor, "An advertisement", "An advertisement")
    assert codes(result) == ["NOT_APPLICABLE", "NOT_APPLICABLE"]
    assert len(api.calls) == 2 and result.metrics.reused_operations == 0


def test_an_empty_cell_fails_the_row_before_the_handler_and_nothing_is_asked() -> None:
    api = FakeApi(good())
    executor = build_live_executor(REGISTRY, text_extraction=options(api))
    result = run(executor, "", "   ")
    assert codes(result) == ["MISSING_VALUE", "MISSING_VALUE"] and api.calls == []


def test_the_answers_are_kept_for_the_time_every_other_kept_value_is() -> None:
    cache = object()
    reused = build_live_executor(
        REGISTRY,
        text_extraction=options(FakeApi()),
        reuse=(cache, object()),  # type: ignore[arg-type]
    )
    policy = reused._policy  # pyright: ignore[reportPrivateUsage]
    assert policy.persistent_cache_ttls == {("text_extraction", SOURCE): RESULT_REUSE_SECONDS}
    # "Nothing found" is an answer and is kept like one; nothing else is.
    assert policy.kept_nothing_found == {("text_extraction", SOURCE)}
    assert policy.memoizable_providers == {("text_extraction", SOURCE)}
    plain = build_live_executor(REGISTRY, text_extraction=options(FakeApi()))
    assert plain._policy.persistent_cache_ttls == {}  # pyright: ignore[reportPrivateUsage]
    assert plain._policy.kept_nothing_found == frozenset()  # pyright: ignore[reportPrivateUsage]


def test_the_configuration_is_part_of_what_a_kept_answer_depends_on() -> None:
    executor = build_live_executor(
        REGISTRY,
        text_extraction=ExtractionOptions(
            api=FakeApi(), model="m2", reasoning_effort="low", usage=ExtractionUsage()
        ),
        reuse=(object(), object()),  # type: ignore[arg-type]
    )
    assert executor._configuration_ids["text_extraction"] == "openai-text-extraction-p1-m2-low"  # pyright: ignore[reportPrivateUsage]


def test_the_values_arrive_typed_in_the_record() -> None:
    api = RowApi(lambda kwargs: good())
    executor = build_live_executor(REGISTRY, text_extraction=options(api))
    result = run(executor, "Total: 9,99 EUR")
    value = result.records[0].row_result.steps[0].result.value
    assert isinstance(value, FoundRecord)
    assert type(value.slots["value_1"].value) is float
    assert value.slots["value_2"].value is None


def test_a_registry_with_the_extraction_and_another_capability_is_refused_at_assembly() -> None:
    mixed = CapabilityRegistry()
    for spec in (*REGISTRY.list_all(), *offered_capabilities(road_distance=False)):
        mixed.register(spec)
    with pytest.raises(ValueError, match="must hold no other capability"):
        build_live_executor(mixed, text_extraction=options(FakeApi([])))
