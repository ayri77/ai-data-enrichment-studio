"""Deterministic validation of enrichment plans."""

from graphlib import CycleError, TopologicalSorter
from dataclasses import dataclass, field
from typing import Literal

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.capability import CapabilitySpec, DataType
from ai_data_enrichment.domain.dataset_profile import (
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    InputReference,
    LiteralValue,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.validation.models import (
    ValidationDiagnostic,
    ValidationResult,
)


_PRIMITIVE_TYPE_MAP: dict[PrimitiveType, DataType] = {
    PrimitiveType.TEXT: DataType.TEXT,
    PrimitiveType.INTEGER: DataType.NUMBER,
    PrimitiveType.NUMBER: DataType.NUMBER,
}


@dataclass(frozen=True)
class TypeResolution:
    """Result of resolving an input's semantic type."""

    status: Literal["resolved", "unsupported", "unknown"]
    data_type: DataType | None = None


@dataclass(frozen=True)
class PlanValidationPolicy:
    """Static restrictions applied during plan validation."""

    protected_columns: frozenset[str] = field(default_factory=frozenset)
    max_steps: int | None = None


class PlanValidator:
    """Validate enrichment plans against available contracts."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        dataset_profile: DatasetProfile,
        policy: PlanValidationPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._dataset_profile = dataset_profile
        self._columns_by_name = {
            column.name: column
            for column in dataset_profile.columns
        } 
        self._policy = policy if policy is not None else PlanValidationPolicy()


    def validate(self, plan: EnrichmentPlan) -> ValidationResult:
        """Validate a plan and collect diagnostics."""
        errors: list[ValidationDiagnostic] = []
        if (
            self._policy.max_steps is not None
            and len(plan.steps) > self._policy.max_steps
        ):
            errors.append(
                ValidationDiagnostic(
                    code="STEP_LIMIT_EXCEEDED",
                    message=(
                        f"Plan contains {len(plan.steps)} steps, exceeding "
                        f"the configured limit of {self._policy.max_steps}."
                    ),
                    field="steps",
                )
            )
        seen_ids: set[str] = set()

        steps_by_id = {step.id: step for step in plan.steps}

        # Validate step IDs and capability contracts.
        for step in plan.steps:
            if step.id in seen_ids:
                errors.append(
                    ValidationDiagnostic(
                        code="DUPLICATE_STEP_ID",
                        message=f"Step ID '{step.id}' is duplicated.",
                        step_id=step.id,
                    )
                )
            else:
                seen_ids.add(step.id)

            capability = self._registry.get(step.capability)

            if capability is None:
                errors.append(
                    ValidationDiagnostic(
                        code="UNSUPPORTED_CAPABILITY",
                        message=f"Capability '{step.capability}' is not registered.",
                        step_id=step.id,
                        field="capability",
                    )
                )
            else:
                errors.extend(
                    self._validate_inputs(step, capability, steps_by_id)
                )
                errors.extend(
                    self._validate_parameters(step, capability)
                )

        # Validate input references.
        for step in plan.steps:
            for input_name, reference in step.inputs.items():
                field = f"inputs.{input_name}"

                if isinstance(reference, ColumnReference):
                    if reference.name not in self._columns_by_name:
                        errors.append(
                            ValidationDiagnostic(
                                code="MISSING_COLUMN_REFERENCE",
                                message=f"Column '{reference.name}' does not exist.",
                                step_id=step.id,
                                field=field,
                            )
                        )

                elif isinstance(reference, StepOutputReference):
                    if reference.step_id not in seen_ids:
                        errors.append(
                            ValidationDiagnostic(
                                code="MISSING_STEP_REFERENCE",
                                message=(
                                    f"Step '{step.id}' references missing "
                                    f"step '{reference.step_id}'."
                                ),
                                step_id=step.id,
                                field=field,
                            )
                        )

        # Validate dependency graph.
        errors.extend(self._validate_cycles(plan))

        # Validate output references.
        errors.extend(self._validate_outputs(plan))

        return ValidationResult(errors=errors)

    def _validate_cycles(self, plan: EnrichmentPlan) -> list[ValidationDiagnostic]:
        """Detect cyclic dependencies between plan steps."""
        step_ids = {step.id for step in plan.steps}

        dependencies = {
            step.id: {
                reference.step_id
                for reference in step.inputs.values()
                if isinstance(reference, StepOutputReference)
                and reference.step_id in step_ids
            }
            for step in plan.steps
        }

        try:
            TopologicalSorter(dependencies).prepare()
        except CycleError as exc:
            cycle = exc.args[1]

            return [
                ValidationDiagnostic(
                    code="CYCLE_DETECTED",
                    message=f"Cyclic dependency: {' -> '.join(cycle)}.",
                    step_id=cycle[0],
                )
            ]

        return []

    def _validate_inputs(
        self,
        step: PlanStep,
        capability: CapabilitySpec,
        steps_by_id: dict[str, PlanStep],
    ) -> list[ValidationDiagnostic]:
        """Validate input names against the capability contract."""
        errors: list[ValidationDiagnostic] = []

        for input_name, input_spec in capability.inputs.items():
            if input_spec.required and input_name not in step.inputs:
                errors.append(
                    ValidationDiagnostic(
                        code="MISSING_REQUIRED_INPUT",
                        message=(
                            f"Capability '{step.capability}' requires "
                            f"input '{input_name}'."
                        ),
                        step_id=step.id,
                        field=f"inputs.{input_name}",
                    )
                )

        for input_name, reference in step.inputs.items():
            input_spec = capability.inputs.get(input_name)

            if input_spec is None:
                errors.append(
                    ValidationDiagnostic(
                        code="UNKNOWN_INPUT",
                        message=(
                            f"Capability '{step.capability}' does not "
                            f"accept input '{input_name}'."
                        ),
                        step_id=step.id,
                        field=f"inputs.{input_name}",
                    )
                )
                continue

            resolution = self._resolve_input_type(reference, steps_by_id)

            if resolution.status == "unsupported":
                errors.append(
                    ValidationDiagnostic(
                        code="UNSUPPORTED_INPUT_TYPE",
                        message=(
                            f"Input '{input_name}' uses a type unsupported "
                            f"by the capability type system."
                        ),
                        step_id=step.id,
                        field=f"inputs.{input_name}",
                    )
                )

            elif (
                resolution.status == "resolved"
                and resolution.data_type is not None
                and resolution.data_type != input_spec.type
            ):
                errors.append(
                    ValidationDiagnostic(
                        code="INPUT_TYPE_MISMATCH",
                        message=(
                            f"Input '{input_name}' expects {input_spec.type.value}, "
                            f"but received {resolution.data_type.value}."
                        ),
                        step_id=step.id,
                        field=f"inputs.{input_name}",
                    )
                )
            elif (
                resolution.status == "unknown"
                and isinstance(reference, ColumnReference)
                and reference.name in self._columns_by_name
            ):
                errors.append(
                    ValidationDiagnostic(
                        code="UNRESOLVED_INPUT_TYPE",
                        message=(
                            f"Cannot determine the type of column "
                            f"'{reference.name}' for input '{input_name}'."
                        ),
                        step_id=step.id,
                        field=f"inputs.{input_name}",
                    )
                )            
        return errors

    def _validate_parameters(
        self,
        step: PlanStep,
        capability: CapabilitySpec,
    ) -> list[ValidationDiagnostic]:
        """Validate parameter names and allowed values."""
        errors: list[ValidationDiagnostic] = []

        for parameter_name, parameter_value in step.parameters.items():
            parameter_spec = capability.parameters.get(parameter_name)

            if parameter_spec is None:
                errors.append(
                    ValidationDiagnostic(
                        code="UNKNOWN_PARAMETER",
                        message=(
                            f"Capability '{step.capability}' does not accept "
                            f"parameter '{parameter_name}'."
                        ),
                        step_id=step.id,
                        field=f"parameters.{parameter_name}",
                    )
                )
                continue

            # Validate the parameter type before checking allowed values.
            if not isinstance(parameter_value, str):
                errors.append(
                    ValidationDiagnostic(
                        code="PARAMETER_TYPE_MISMATCH",
                        message=(
                            f"Parameter '{parameter_name}' expects Text, "
                            f"but received {type(parameter_value).__name__}."
                        ),
                        step_id=step.id,
                        field=f"parameters.{parameter_name}",
                    )
                )
                continue

            if parameter_value not in parameter_spec.allowed_values:
                errors.append(
                    ValidationDiagnostic(
                        code="INVALID_PARAMETER_VALUE",
                        message=(
                            f"Parameter '{parameter_name}' has invalid value "
                            f"'{parameter_value}'. Allowed values: "
                            f"{parameter_spec.allowed_values}."
                        ),
                        step_id=step.id,
                        field=f"parameters.{parameter_name}",
                    )
                )

        return errors

    def _validate_outputs(
        self,
        plan: EnrichmentPlan,
    ) -> list[ValidationDiagnostic]:
        """Validate output step references and selected fields."""
        errors: list[ValidationDiagnostic] = []
        steps_by_id = {step.id: step for step in plan.steps}
        seen_targets: set[str] = set()

        for index, output in enumerate(plan.outputs):
            if output.target_column in seen_targets:
                errors.append(
                    ValidationDiagnostic(
                        code="DUPLICATE_OUTPUT_TARGET",
                        message=(
                            f"Multiple output mappings target column "
                            f"'{output.target_column}'."
                        ),
                        step_id=output.step_id,
                        field=f"outputs[{index}].target_column",
                    )
                )
            else:
                seen_targets.add(output.target_column)

            if output.target_column in self._policy.protected_columns:
                errors.append(
                    ValidationDiagnostic(
                        code="PROTECTED_OUTPUT_TARGET",
                        message=(
                            f"Output target column '{output.target_column}' "
                            f"is protected against writes."
                        ),
                        step_id=output.step_id,
                        field=f"outputs[{index}].target_column",
                    )
                )            
            
            source_step = steps_by_id.get(output.step_id)

            if source_step is None:
                errors.append(
                    ValidationDiagnostic(
                        code="MISSING_STEP_REFERENCE",
                        message=(
                            f"Output mapping references missing step "
                            f"'{output.step_id}'."
                        ),
                        step_id=output.step_id,
                        field=f"outputs[{index}].step_id",
                    )
                )
                continue

            capability = self._registry.get(source_step.capability)

            # Unsupported capabilities are reported during step validation.
            if capability is None:
                continue

            if output.value_field not in capability.output.fields:
                errors.append(
                    ValidationDiagnostic(
                        code="UNKNOWN_OUTPUT_FIELD",
                        message=(
                            f"Capability '{source_step.capability}' does not "
                            f"expose output field '{output.value_field}'."
                        ),
                        step_id=output.step_id,
                        field=f"outputs[{index}].value_field",
                    )
                )

        return errors

    def _resolve_input_type(
        self,
        reference: InputReference,
        steps_by_id: dict[str, PlanStep],
    ) -> TypeResolution:
        """Resolve the semantic type of an input reference."""

        if isinstance(reference, ColumnReference):
            column = self._columns_by_name.get(reference.name)

            if column is None:
                return TypeResolution(status="unknown")

            data_type = _PRIMITIVE_TYPE_MAP.get(column.inferred_type)

            if data_type is not None:
                return TypeResolution(status="resolved", data_type=data_type)

            if column.inferred_type in (
                PrimitiveType.UNKNOWN,
                PrimitiveType.MIXED,
            ):
                return TypeResolution(status="unknown")

            return TypeResolution(status="unsupported")

        if isinstance(reference, StepOutputReference):
            source_step = steps_by_id.get(reference.step_id)

            if source_step is None:
                return TypeResolution(status="unknown")

            source_capability = self._registry.get(source_step.capability)

            if source_capability is None:
                return TypeResolution(status="unknown")

            return TypeResolution(
                status="resolved",
                data_type=source_capability.output.type,
            )

        if isinstance(reference, LiteralValue):
            value = reference.value

            if isinstance(value, str):
                return TypeResolution(
                    status="resolved",
                    data_type=DataType.TEXT,
                )

            if isinstance(value, bool):
                return TypeResolution(status="unsupported")

            if isinstance(value, (int, float)):
                return TypeResolution(
                    status="resolved",
                    data_type=DataType.NUMBER,
                )

            return TypeResolution(status="unsupported")
