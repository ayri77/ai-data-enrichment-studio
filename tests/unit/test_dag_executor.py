"""Tests for executing validated DAG plans."""

from collections.abc import Mapping

import pytest

from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    LiteralValue,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.executor import DAGExecutor
from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)


def test_executor_runs_dependency_chain_in_order() -> None:
    """Execute reversed plan steps in their dependency order."""

    calls: list[str] = []

    class ResolveHandler:
        """Return a controlled entity identity."""

        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            calls.append("resolve")

            assert inputs["name"] == "Everest"
            assert inputs["country"] == "Nepal"
            assert parameters["entity_type"] == "mountain"

            return OperationResult(
                status="success",
                value=EntityRef(
                    source="wikidata",
                    entity_id="Q513",
                ),
            )

    class LookupHandler:
        """Return a controlled property value."""

        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            calls.append("lookup")

            assert inputs["entity"] == EntityRef(
                source="wikidata",
                entity_id="Q513",
            )
            assert parameters["property"] == "elevation"

            return OperationResult(
                status="success",
                value=Quantity(value=8848.86, unit="m"),
            )

    # Deliberately list the dependent operation first.
    plan = EnrichmentPlan(
        version="1.0",
        steps=[
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
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": ColumnReference(
                        kind="column",
                        name="mountain",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Nepal",
                    ),
                },
                parameters={"entity_type": "mountain"},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="lookup",
                value_field="value",
                target_column="elevation_m",
            ),
        ],
        assumptions=[],
    )

    handlers: dict[str, CapabilityHandler] = {
        "resolve_entity": ResolveHandler(),
        "lookup_property": LookupHandler(),
    }

    executor = DAGExecutor(handlers)
    source_row = {"mountain": "Everest"}

    result = executor.execute_row(plan, source_row)

    assert calls == ["resolve", "lookup"]

    assert [step.step_id for step in result.steps] == [
        "resolve",
        "lookup",
    ]

    assert result.steps[0].result.value == EntityRef(
        source="wikidata",
        entity_id="Q513",
    )

    assert result.steps[1].result.value == Quantity(
        value=8848.86,
        unit="m",
    )

    # Execution must not modify the original input record.
    assert source_row == {"mountain": "Everest"}


def test_executor_isolates_failed_dependencies() -> None:
    """Block dependent steps while executing independent branches."""

    calls: list[str] = []

    class FailingResolveHandler:
        """Simulate an entity that cannot be found."""

        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            calls.append("resolve")

            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="Entity could not be resolved",
            )

    class LookupHandler:
        """This handler must never run after resolution fails."""

        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            calls.append("lookup")
            raise AssertionError("Lookup must not execute")

    class GeocodeHandler:
        """Execute an independent operation successfully."""

        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            calls.append("geocode")

            return OperationResult(
                status="success",
                value=GeoPoint(latitude=53.55, longitude=10.0),
            )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            # Dependent step deliberately appears first.
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
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": LiteralValue(
                        kind="literal",
                        value="Unknown Mountain",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Nepal",
                    ),
                },
                parameters={"entity_type": "mountain"},
            ),
            PlanStep(
                id="geocode",
                capability="geocode",
                inputs={
                    "city": LiteralValue(
                        kind="literal",
                        value="Hamburg",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Germany",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="lookup",
                value_field="value",
                target_column="elevation_m",
            ),
        ],
        assumptions=[],
    )

    handlers: dict[str, CapabilityHandler] = {
        "resolve_entity": FailingResolveHandler(),
        "lookup_property": LookupHandler(),
        "geocode": GeocodeHandler(),
    }

    executor = DAGExecutor(handlers)

    result = executor.execute_row(plan, row={})

    outcomes = {step.step_id: step.result for step in result.steps}

    assert len(outcomes) == 3

    # The original provider failure is preserved.
    assert outcomes["resolve"].status == "failure"
    assert outcomes["resolve"].error_code == "NOT_FOUND"

    # Dependent operation is blocked, not executed.
    assert outcomes["lookup"].status == "failure"
    assert outcomes["lookup"].error_code == "DEPENDENCY_FAILED"
    assert "resolve" in (outcomes["lookup"].message or "")

    # Unrelated operation still succeeds.
    assert outcomes["geocode"].status == "success"
    assert outcomes["geocode"].value == GeoPoint(
        latitude=53.55,
        longitude=10.0,
    )

    assert set(calls) == {"resolve", "geocode"}
    assert "lookup" not in calls


def test_executor_isolates_missing_input_column() -> None:
    """Missing row data must not prevent independent DAG operations."""

    calls: list[str] = []

    class GeocodeHandler:
        """Return coordinates for inputs that can be resolved."""

        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            city = inputs["city"]
            assert isinstance(city, str)

            calls.append(city)

            return OperationResult(
                status="success",
                value=GeoPoint(latitude=53.55, longitude=10.0),
            )

    class DistanceHandler:
        """Must not run when a required coordinate is missing."""

        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            raise AssertionError("Distance handler must not execute")

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(
                        kind="step_output",
                        step_id="missing_city",
                    ),
                    "point_b": StepOutputReference(
                        kind="step_output",
                        step_id="other_city",
                    ),
                },
                parameters={"unit": "km"},
            ),
            PlanStep(
                id="missing_city",
                capability="geocode",
                inputs={
                    "city": ColumnReference(
                        kind="column",
                        name="city",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Germany",
                    ),
                },
                parameters={},
            ),
            PlanStep(
                id="other_city",
                capability="geocode",
                inputs={
                    "city": LiteralValue(
                        kind="literal",
                        value="Hamburg",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Germany",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="distance",
                value_field="value",
                target_column="distance_km",
            ),
        ],
        assumptions=[],
    )

    handlers: dict[str, CapabilityHandler] = {
        "geocode": GeocodeHandler(),
        "geodesic_distance": DistanceHandler(),
    }

    executor = DAGExecutor(handlers)

    # The row contains no "city" key.
    source_row: dict[str, RuntimeValue] = {}

    result = executor.execute_row(plan, source_row)

    outcomes = {step.step_id: step.result for step in result.steps}

    assert len(outcomes) == 3

    assert outcomes["missing_city"].status == "failure"
    assert outcomes["missing_city"].error_code == "MISSING_COLUMN"
    assert "city" in (outcomes["missing_city"].message or "")

    assert outcomes["other_city"].status == "success"

    assert outcomes["distance"].status == "failure"
    assert outcomes["distance"].error_code == "DEPENDENCY_FAILED"

    # Only the independent geocode operation was invoked.
    assert calls == ["Hamburg"]

    # The original row remains unchanged.
    assert source_row == {}


def test_executor_isolates_missing_cell_value() -> None:
    """A null cell must not reach a handler or stop independent steps."""

    calls: list[RuntimeValue] = []

    class GeocodeHandler:
        """Return a controlled coordinate for valid input."""

        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            calls.append(inputs["city"])

            return OperationResult(
                status="success",
                value=GeoPoint(latitude=53.55, longitude=10.0),
            )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="blank_city",
                capability="geocode",
                inputs={
                    "city": ColumnReference(
                        kind="column",
                        name="city",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Germany",
                    ),
                },
                parameters={},
            ),
            PlanStep(
                id="other_city",
                capability="geocode",
                inputs={
                    "city": LiteralValue(
                        kind="literal",
                        value="Hamburg",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Germany",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="other_city",
                value_field="value",
                target_column="coordinates",
            ),
        ],
        assumptions=[],
    )

    executor = DAGExecutor(
        handlers={"geocode": GeocodeHandler()},
    )

    # A present column with an empty cell is different from an absent key.
    source_row: dict[str, RuntimeValue | None] = {"city": None}

    result = executor.execute_row(plan, source_row)

    outcomes = {step.step_id: step.result for step in result.steps}

    assert outcomes["blank_city"].status == "failure"
    assert outcomes["blank_city"].error_code == "MISSING_VALUE"
    assert "city" in (outcomes["blank_city"].message or "")

    assert outcomes["other_city"].status == "success"

    # The handler must only receive the valid independent operation.
    assert calls == ["Hamburg"]

    assert source_row == {"city": None}


@pytest.mark.parametrize(
    "invalid_city",
    [True, float("nan"), float("inf"), float("-inf"), 10**1000],
)
def test_executor_isolates_unsupported_literal(invalid_city: bool | float | int) -> None:
    """Invalid literals must not stop independent DAG branches."""

    calls: list[str] = []

    class GeocodeHandler:
        """Return coordinates for valid textual inputs."""

        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            city = inputs["city"]
            assert isinstance(city, str)

            calls.append(city)

            return OperationResult(
                status="success",
                value=GeoPoint(latitude=53.55, longitude=10.0),
            )

    def make_step(step_id: str, city: str | bool | float | int) -> PlanStep:
        """Build a geocode step with a literal city value."""
        return PlanStep(
            id=step_id,
            capability="geocode",
            inputs={
                "city": LiteralValue(kind="literal", value=city),
                "country": LiteralValue(
                    kind="literal",
                    value="Germany",
                ),
            },
            parameters={},
        )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            make_step("invalid_city", invalid_city),
            make_step("valid_city", "Hamburg"),
        ],
        outputs=[
            OutputMapping(
                step_id="valid_city",
                value_field="value",
                target_column="coordinates",
            ),
        ],
        assumptions=[],
    )

    executor = DAGExecutor(
        handlers={"geocode": GeocodeHandler()},
    )

    result = executor.execute_row(plan, row={})

    outcomes = {step.step_id: step.result for step in result.steps}

    assert outcomes["invalid_city"].status == "failure"
    assert outcomes["invalid_city"].error_code == "UNSUPPORTED_LITERAL"

    assert outcomes["valid_city"].status == "success"
    assert outcomes["valid_city"].value == GeoPoint(
        latitude=53.55,
        longitude=10.0,
    )

    # Only the valid branch should invoke the handler.
    assert calls == ["Hamburg"]


def test_executor_runs_single_step_and_preserves_evidence() -> None:
    """Keep handler provenance when a single independent step succeeds."""

    calls: list[str] = []
    evidence = Evidence(source="stub", reference="geocode/hamburg")

    class GeocodeHandler:
        """Return an evidenced result without external requests."""

        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            assert inputs == {"city": "Hamburg", "country": "Germany"}
            assert parameters == {}
            calls.append("geocode")
            return OperationResult(
                status="success",
                value=GeoPoint(latitude=53.55, longitude=10.0),
                evidence=(evidence,),
            )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="geocode",
                capability="geocode",
                inputs={
                    "city": LiteralValue(kind="literal", value="Hamburg"),
                    "country": LiteralValue(kind="literal", value="Germany"),
                },
                parameters={},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="geocode",
                value_field="value",
                target_column="coordinates",
            ),
        ],
        assumptions=[],
    )

    result = DAGExecutor(handlers={"geocode": GeocodeHandler()}).execute_row(plan, row={})

    assert calls == ["geocode"]
    assert len(result.steps) == 1
    assert result.steps[0].step_id == "geocode"
    assert result.steps[0].result.status == "success"
    assert result.steps[0].result.evidence == (evidence,)


@pytest.mark.parametrize("numeric_literal", [42, 42.5])
def test_executor_converts_finite_numeric_literals(numeric_literal: int | float) -> None:
    """Present JSON numbers to handlers as RuntimeValue floats."""

    class NumericHandler:
        def execute(
            self,
            *,
            inputs: Mapping[str, RuntimeValue],
            parameters: Mapping[str, str],
        ) -> OperationResult:
            assert isinstance(inputs["number"], float)
            assert inputs["number"] == float(numeric_literal)
            assert parameters == {}
            return OperationResult(status="success", value=inputs["number"])

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="number",
                capability="identity_number",
                inputs={"number": LiteralValue(kind="literal", value=numeric_literal)},
                parameters={},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="number",
                value_field="value",
                target_column="number_out",
            ),
        ],
        assumptions=[],
    )

    result = DAGExecutor(handlers={"identity_number": NumericHandler()}).execute_row(plan, row={})

    assert len(result.steps) == 1
    assert result.steps[0].result.status == "success"
    assert result.steps[0].result.value == float(numeric_literal)
