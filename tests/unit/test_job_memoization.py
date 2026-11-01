"""Unit tests for bounded job-scoped success-only operation reuse."""

from collections.abc import Mapping
from typing import cast

import pytest

from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.job_models import JobExecutionPolicy
from ai_data_enrichment.execution.memoization import JobMemoization
from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)


class RecordingHandler:
    """Return a controlled result and capture the actual handler invocations."""

    def __init__(self, *, failure: bool = False) -> None:
        self.failure = failure
        self.requests: list[tuple[dict[str, RuntimeValue], dict[str, str]]] = []

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        self.requests.append((dict(inputs), dict(parameters)))
        if self.failure:
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="No trusted match.",
            )
        return OperationResult(
            status="success",
            value=EntityRef(source="wikidata", entity_id="Q513"),
            evidence=(Evidence(source="wikidata", reference="Q513:P31"),),
        )


def policy(*, capacity: int = 10) -> JobExecutionPolicy:
    return JobExecutionPolicy(
        max_memoized_results=capacity,
        memoizable_providers=frozenset({("resolve_entity", "wikidata")}),
    )


def wrap(
    session: JobMemoization,
    handler: CapabilityHandler,
    *,
    capability: str = "resolve_entity",
    provider: str = "wikidata",
    config: str = "wikidata-config-v1",
) -> CapabilityHandler:
    return session.wrap_handler(
        capability_id=capability,
        provider_id=provider,
        configuration_id=config,
        handler=handler,
    )


def test_successful_operation_reused_with_evidence_and_reordered_mappings() -> None:
    session = JobMemoization(policy())
    handler = RecordingHandler()
    memo = wrap(session, handler)
    first = memo.execute(
        inputs={"name": "Everest", "country": "Nepal"},
        parameters={"entity_type": "mountain", "language": "en"},
    )
    second = memo.execute(
        inputs={"country": "Nepal", "name": "Everest"},
        parameters={"language": "en", "entity_type": "mountain"},
    )

    assert first is second
    assert first.evidence == (Evidence(source="wikidata", reference="Q513:P31"),)
    assert len(handler.requests) == 1
    assert session.snapshot_metrics().model_dump() == {
        "total_rows": 0,
        "successful_rows": 0,
        "partial_rows": 0,
        "failed_rows": 0,
        "logical_operations": 2,
        "executed_handler_calls": 1,
        "reused_operations": 1,
        "memoized_results": 1,
        "memo_capacity_skips": 0,
    }


def test_no_implicit_semantic_name_normalization_or_parameter_collision() -> None:
    session = JobMemoization(policy())
    handler = RecordingHandler()
    memo = wrap(session, handler)

    for name, parameters in [
        ("Berlin", {"language": "en"}),
        (" berlin ", {"language": "en"}),
        ("Berlin", {"language": "de"}),
        ("Berlin", {"language": "en"}),
    ]:
        memo.execute(inputs={"name": name}, parameters=parameters)

    assert len(handler.requests) == 3
    assert session.snapshot_metrics().reused_operations == 1


def test_explicit_eligibility_prevents_unapproved_provider_reuse() -> None:
    session = JobMemoization(policy())
    handler = RecordingHandler()
    unapproved = wrap(session, handler, capability="geocode", provider="open-meteo")
    for _ in range(2):
        unapproved.execute(inputs={"name": "Berlin"}, parameters={})

    assert len(handler.requests) == 2
    assert session.snapshot_metrics().memoized_results == 0
    assert session.snapshot_metrics().executed_handler_calls == 2


def test_failure_is_never_reused() -> None:
    session = JobMemoization(policy())
    handler = RecordingHandler(failure=True)
    memo = wrap(session, handler)
    for _ in range(2):
        result = memo.execute(inputs={"name": "Unknown Mountain"}, parameters={})
        assert result.status == "failure"
        assert result.error_code == "NOT_FOUND"

    assert len(handler.requests) == 2
    assert session.snapshot_metrics().reused_operations == 0
    assert session.snapshot_metrics().memoized_results == 0


def test_capacity_ceiling_does_not_evict_existing_hits() -> None:
    session = JobMemoization(policy(capacity=1))
    handler = RecordingHandler()
    memo = wrap(session, handler)

    for name in ["A", "B", "A", "B"]:
        memo.execute(inputs={"name": name}, parameters={})

    assert [request[0]["name"] for request in handler.requests] == ["A", "B", "B"]
    metrics = session.snapshot_metrics()
    assert metrics.logical_operations == 4
    assert metrics.executed_handler_calls == 3
    assert metrics.reused_operations == 1
    assert metrics.memoized_results == 1
    assert metrics.memo_capacity_skips == 2


def test_zero_capacity_disables_reuse_without_disabling_execution() -> None:
    session = JobMemoization(policy(capacity=0))
    handler = RecordingHandler()
    memo = wrap(session, handler)
    for _ in range(2):
        memo.execute(inputs={"name": "Everest"}, parameters={})
    assert len(handler.requests) == 2
    assert session.snapshot_metrics().memo_capacity_skips == 2


def test_provider_and_configuration_identity_prevent_cross_binding_collision() -> None:
    session = JobMemoization(
        JobExecutionPolicy(
            memoizable_providers=frozenset(
                {("resolve_entity", "wikidata"), ("resolve_entity", "other")}
            )
        )
    )
    handler = RecordingHandler()
    a = wrap(session, handler, config="a")
    b = wrap(session, handler, config="b")
    c = wrap(session, handler, provider="other", config="a")
    inputs = {"name": "Everest"}

    for wrapped in [a, b, c, a, b, c]:
        wrapped.execute(inputs=inputs, parameters={})

    assert len(handler.requests) == 3
    assert session.snapshot_metrics().reused_operations == 3


@pytest.mark.parametrize(
    "first, second",
    [
        ({"value": 1.0}, {"value": "1.0"}),
        ({"value": EntityRef(source="wikidata", entity_id="Q513")},
         {"value": EntityRef(source="another", entity_id="Q513")}),
        ({"value": Quantity(value=12.0, unit="m")},
         {"value": Quantity(value=12.0, unit="km")}),
        ({"value": GeoPoint(latitude=1.0, longitude=2.0)},
         {"value": GeoPoint(latitude=2.0, longitude=1.0)}),
        ({"value": 1.0}, {"value": 2.0}),
    ],
)
def test_typed_input_changes_are_distinct(
    first: dict[str, RuntimeValue],
    second: dict[str, RuntimeValue],
) -> None:
    session = JobMemoization(policy())
    handler = RecordingHandler()
    memo = wrap(session, handler)
    memo.execute(inputs=first, parameters={})
    memo.execute(inputs=second, parameters={})
    memo.execute(inputs=first, parameters={})

    assert len(handler.requests) == 2
    assert session.snapshot_metrics().reused_operations == 1


def test_different_capabilities_do_not_collide_even_with_same_provider() -> None:
    session = JobMemoization(
        JobExecutionPolicy(
            memoizable_providers=frozenset(
                {("resolve_entity", "wikidata"), ("lookup_property", "wikidata")}
            )
        )
    )
    handler = RecordingHandler()
    resolve = wrap(session, handler)
    lookup = wrap(session, handler, capability="lookup_property")

    resolve.execute(inputs={"name": "Everest"}, parameters={})
    lookup.execute(inputs={"name": "Everest"}, parameters={})
    assert len(handler.requests) == 2


def test_separate_sessions_never_share_previous_job_results() -> None:
    handler = RecordingHandler()
    for _ in range(2):
        session = JobMemoization(policy())
        memo = wrap(session, handler)
        memo.execute(inputs={"name": "Everest"}, parameters={})
        memo.execute(inputs={"name": "Everest"}, parameters={})
        assert session.snapshot_metrics().reused_operations == 1

    assert len(handler.requests) == 2


def test_invalid_runtime_inputs_bypass_cache_not_handler() -> None:
    session = JobMemoization(policy())
    handler = RecordingHandler()
    memo = wrap(session, handler)
    # This deliberately violates the static handler contract to verify that
    # memoization cannot generate a reusable key for invalid runtime data.
    invalid_inputs = cast(Mapping[str, RuntimeValue], {"value": float("nan")})
    for _ in range(2):
        memo.execute(inputs=invalid_inputs, parameters={})
    assert len(handler.requests) == 2
    assert session.snapshot_metrics().memoized_results == 0


def test_wrapper_checks_identity_and_handler_shape() -> None:
    session = JobMemoization(policy())
    handler = RecordingHandler()
    for capability, provider, config in [
        ("", "wikidata", "stable"),
        ("resolve_entity", " ", "stable"),
        ("resolve_entity", "wikidata", ""),
    ]:
        with pytest.raises(ValueError, match="not be blank"):
            wrap(session, handler, capability=capability, provider=provider, config=config)

    with pytest.raises(TypeError, match="implement execute"):
        wrap(session, cast(CapabilityHandler, object()))


def test_empty_session_has_valid_zero_metrics() -> None:
    assert JobMemoization(policy()).snapshot_metrics().logical_operations == 0
