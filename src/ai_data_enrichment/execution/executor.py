"""Execute a validated enrichment DAG for one dataset row."""

from collections.abc import Mapping
from math import isfinite

from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    InputReference,
    LiteralValue,
    StepOutputReference,
)
from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.dag import order_plan_steps
from ai_data_enrichment.execution.models import (
    OperationResult,
    RowExecutionResult,
    RuntimeValue,
    StepExecutionResult,
)


class UnsupportedLiteralError(ValueError):
    """Indicate that a plan literal cannot be used as a runtime value."""


def _resolve_input(
    reference: InputReference,
    row: Mapping[str, RuntimeValue | None],
    completed: Mapping[str, OperationResult],
) -> RuntimeValue:
    """Resolve a plan reference into a typed value for a capability handler."""
    if isinstance(reference, ColumnReference):
        value = row[reference.name]
        if value is None:
            raise ValueError(f"Column '{reference.name}' has no value.")
        return value

    if isinstance(reference, StepOutputReference):
        result = completed[reference.step_id]
        if result.status != "success" or result.value is None:
            raise ValueError(f"Step '{reference.step_id}' has no successful output.")
        return result.value

    if isinstance(reference, LiteralValue):
        value = reference.value
        if isinstance(value, str):
            return value
        # bool is a subclass of int, but it is not a supported runtime number.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                number = float(value)
            except OverflowError as exc:
                raise UnsupportedLiteralError("Numeric literal is out of runtime range.") from exc

            if not isfinite(number):
                raise UnsupportedLiteralError("Numeric literal must be finite.")

            return number
        raise UnsupportedLiteralError(
            f"Unsupported literal value of type '{type(value).__name__}'."
        )

    raise TypeError(f"Unsupported input reference: {type(reference).__name__}.")


class DAGExecutor:
    """Run registered capability handlers in dependency order for one row."""

    def __init__(self, handlers: Mapping[str, CapabilityHandler]) -> None:
        self._handlers = dict(handlers)

    def execute_row(
        self,
        plan: EnrichmentPlan,
        row: Mapping[str, RuntimeValue | None],
    ) -> RowExecutionResult:
        """Execute a validated plan without modifying the source row."""
        completed: dict[str, OperationResult] = {}
        outcomes: list[StepExecutionResult] = []

        def record_outcome(step_id: str, result: OperationResult) -> None:
            """Keep dependency state and the ordered result list in sync."""
            completed[step_id] = result
            outcomes.append(StepExecutionResult(step_id=step_id, result=result))

        for step in order_plan_steps(plan):
            # Do not execute a node whose prerequisite failed. Other DAG branches
            # remain executable because they have no dependency on this result.
            failed_dependencies = [
                reference.step_id
                for reference in step.inputs.values()
                if isinstance(reference, StepOutputReference)
                and (
                    reference.step_id not in completed
                    or completed[reference.step_id].status != "success"
                )
            ]
            if failed_dependencies:
                record_outcome(
                    step.id,
                    OperationResult(
                        status="failure",
                        error_code="DEPENDENCY_FAILED",
                        message=(
                            "Required step output is unavailable: "
                            + ", ".join(failed_dependencies)
                            + "."
                        ),
                    ),
                )
                continue

            # A missing column and an empty cell are different data problems.
            missing_columns = [
                reference.name
                for reference in step.inputs.values()
                if isinstance(reference, ColumnReference) and reference.name not in row
            ]
            if missing_columns:
                record_outcome(
                    step.id,
                    OperationResult(
                        status="failure",
                        error_code="MISSING_COLUMN",
                        message="Missing input column(s): " + ", ".join(missing_columns) + ".",
                    ),
                )
                continue

            missing_values = [
                reference.name
                for reference in step.inputs.values()
                if isinstance(reference, ColumnReference) and row[reference.name] is None
            ]
            if missing_values:
                record_outcome(
                    step.id,
                    OperationResult(
                        status="failure",
                        error_code="MISSING_VALUE",
                        message="Empty input value(s): " + ", ".join(missing_values) + ".",
                    ),
                )
                continue

            handler = self._handlers.get(step.capability)
            if handler is None:
                raise ValueError(f"No handler registered for capability '{step.capability}'.")

            # PlanValidator checks parameter types before execution. Keep the
            # runtime guard so a bypassed validator cannot pass JSON objects
            # to a handler that expects string parameters.
            parameters: dict[str, str] = {}
            for name, value in step.parameters.items():
                if not isinstance(value, str):
                    raise ValueError(f"Parameter '{name}' must be a string.")
                parameters[name] = value

            try:
                resolved_inputs = {
                    name: _resolve_input(reference, row, completed)
                    for name, reference in step.inputs.items()
                }
            except UnsupportedLiteralError as exc:
                record_outcome(
                    step.id,
                    OperationResult(
                        status="failure",
                        error_code="UNSUPPORTED_LITERAL",
                        message=str(exc),
                    ),
                )
                continue

            # Handlers communicate expected operational failures through
            # OperationResult; do not turn arbitrary programming errors into
            # operational failures here.
            result = handler.execute(inputs=resolved_inputs, parameters=parameters)
            record_outcome(step.id, result)

        return RowExecutionResult(steps=tuple(outcomes))
