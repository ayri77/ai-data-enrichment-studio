"""Test the validation of enrichment plans."""

import pytest

from ai_data_enrichment.capabilities.definitions import (
    get_geocode_spec,
    get_lookup_property_spec,
    get_resolve_entity_spec,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    LiteralValue,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.validation.models import ValidationDiagnostic, ValidationResult
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy, PlanValidator


@pytest.mark.parametrize("max_steps", [-1, -10])
def test_plan_validation_policy_rejects_negative_step_limit(max_steps: int) -> None:
    """Reject invalid policies rather than reporting misleading plan errors."""
    with pytest.raises(ValueError, match="max_steps must be non-negative"):
        PlanValidationPolicy(max_steps=max_steps)


def test_validation_result_validity_depends_on_errors() -> None:
    """Validation result validity should be derived from diagnostics."""
    valid_result = ValidationResult(errors=[])

    diagnostic = ValidationDiagnostic(
        code="DUPLICATE_STEP_ID",
        message="Step ID 'origin' is duplicated.",
        step_id="origin",
    )

    invalid_result = ValidationResult(errors=[diagnostic])

    assert valid_result.valid is True
    assert invalid_result.valid is False
    assert invalid_result.errors == [diagnostic]

    # The computed validity must also appear in serialized output.
    assert invalid_result.model_dump(mode="json")["valid"] is False


def test_plan_validator_rejects_duplicate_step_ids() -> None:
    """Reject plans containing duplicate step identifiers."""
    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={},
                parameters={},
            ),
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={},
                parameters={},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=CapabilityRegistry(),
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    duplicates = [error for error in result.errors if error.code == "DUPLICATE_STEP_ID"]

    assert result.valid is False
    assert len(duplicates) == 1
    assert duplicates[0].step_id == "origin"


def test_plan_validator_allows_repeated_capability_with_unique_step_ids() -> None:
    """Allow multiple steps using the same capability with unique IDs."""
    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={},
                parameters={},
            ),
            PlanStep(
                id="destination",
                capability="geocode",
                inputs={},
                parameters={},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=CapabilityRegistry(),
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    assert not any(error.code == "DUPLICATE_STEP_ID" for error in result.errors)


def test_plan_validator_rejects_missing_step_reference() -> None:
    """Reject references to steps that do not exist in the plan."""
    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="retrieve_elevation",
                capability="lookup_property",
                inputs={
                    "entity": StepOutputReference(
                        kind="step_output",
                        step_id="resolve_mountain",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=CapabilityRegistry(),
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "MISSING_STEP_REFERENCE"]

    assert len(errors) == 1
    assert errors[0].step_id == "retrieve_elevation"
    assert errors[0].field == "inputs.entity"
    assert "resolve_mountain" in errors[0].message


@pytest.mark.parametrize(
    "step_ids",
    [
        ["a"],
        ["a", "b", "c"],
    ],
    ids=["self-reference", "three-step-cycle"],
)
def test_plan_validator_detects_cycles(step_ids: list[str]) -> None:
    """Detect cycles regardless of their length."""
    steps = [
        PlanStep(
            id=step_id,
            capability="geocode",
            inputs={
                "dependency": StepOutputReference(
                    kind="step_output",
                    step_id=step_ids[(index + 1) % len(step_ids)],
                ),
            },
            parameters={},
        )
        for index, step_id in enumerate(step_ids)
    ]

    plan = EnrichmentPlan(
        version="1.0",
        steps=steps,
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=CapabilityRegistry(),
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    cycles = [error for error in result.errors if error.code == "CYCLE_DETECTED"]

    assert len(cycles) == 1


def test_plan_validator_accepts_acyclic_dependencies() -> None:
    """Accept a dependency graph with multiple predecessors."""
    steps = [
        PlanStep(
            id="origin",
            capability="geocode",
            inputs={},
            parameters={},
        ),
        PlanStep(
            id="destination",
            capability="geocode",
            inputs={},
            parameters={},
        ),
        PlanStep(
            id="distance",
            capability="geodesic_distance",
            inputs={
                name: StepOutputReference(
                    kind="step_output",
                    step_id=source,
                )
                for name, source in {
                    "point_a": "origin",
                    "point_b": "destination",
                }.items()
            },
            parameters={},
        ),
    ]

    plan = EnrichmentPlan(
        version="1.0",
        steps=steps,
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=CapabilityRegistry(),
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    assert not any(error.code == "CYCLE_DETECTED" for error in result.errors)


def test_plan_validator_rejects_missing_column_reference() -> None:
    """Reject references to columns absent from the dataset profile."""
    profile = DatasetProfile(
        dataset_id="mountains",
        name="Mountains",
        columns=[
            ColumnProfile(
                name="Mountain",
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
            ),
        ],
    )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="resolve_mountain",
                capability="resolve_entity",
                inputs={
                    "name": ColumnReference(
                        kind="column",
                        name="Mountain",
                    ),
                    "country": ColumnReference(
                        kind="column",
                        name="Country",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=CapabilityRegistry(),
        dataset_profile=profile,
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "MISSING_COLUMN_REFERENCE"]

    assert len(errors) == 1
    assert errors[0].step_id == "resolve_mountain"
    assert errors[0].field == "inputs.country"
    assert "Country" in errors[0].message


def test_plan_validator_rejects_unsupported_capability() -> None:
    """Reject steps whose capability is not registered."""
    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="mystery",
                capability="unknown_capability",
                inputs={},
                parameters={},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=CapabilityRegistry(),
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "UNSUPPORTED_CAPABILITY"]

    assert len(errors) == 1
    assert errors[0].step_id == "mystery"
    assert errors[0].field == "capability"


def test_plan_validator_rejects_missing_required_input() -> None:
    """Reject capability calls with missing required inputs."""
    registry = CapabilityRegistry()
    registry.register(get_geocode_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={
                    "city": LiteralValue(
                        kind="literal",
                        value="Berlin",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "MISSING_REQUIRED_INPUT"]

    assert len(errors) == 1
    assert errors[0].step_id == "origin"
    assert errors[0].field == "inputs.country"


def test_plan_validator_rejects_unknown_input() -> None:
    """Reject input arguments not declared by the capability."""
    registry = CapabilityRegistry()
    registry.register(get_geocode_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={
                    "city": LiteralValue(
                        kind="literal",
                        value="Berlin",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Germany",
                    ),
                    "postal_code": LiteralValue(
                        kind="literal",
                        value="10115",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "UNKNOWN_INPUT"]

    assert len(errors) == 1
    assert errors[0].step_id == "origin"
    assert errors[0].field == "inputs.postal_code"


def test_plan_validator_rejects_incompatible_step_output_type() -> None:
    """Reject step outputs incompatible with expected input types."""
    registry = CapabilityRegistry()
    registry.register(get_geocode_spec())
    registry.register(get_lookup_property_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={
                    "city": LiteralValue(kind="literal", value="Berlin"),
                    "country": LiteralValue(kind="literal", value="Germany"),
                },
                parameters={},
            ),
            PlanStep(
                id="retrieve_property",
                capability="lookup_property",
                inputs={
                    "entity": StepOutputReference(
                        kind="step_output",
                        step_id="origin",
                    ),
                },
                parameters={"property": "elevation"},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "INPUT_TYPE_MISMATCH"]

    assert len(errors) == 1
    assert errors[0].step_id == "retrieve_property"
    assert errors[0].field == "inputs.entity"
    assert "GeoPoint" in errors[0].message
    assert "EntityRef" in errors[0].message


def test_plan_validator_rejects_incompatible_column_type() -> None:
    """Reject dataset columns incompatible with capability input types."""
    registry = CapabilityRegistry()
    registry.register(get_geocode_spec())

    dataset_profile = DatasetProfile(
        dataset_id="test",
        name="Test dataset",
        columns=[
            ColumnProfile(
                name="city_code",
                inferred_type=PrimitiveType.NUMBER,
                nullable=False,
                null_count=0,
                sample_values=[123],
                warnings=[],
            ),
        ],
    )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={
                    "city": ColumnReference(
                        kind="column",
                        name="city_code",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Germany",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=dataset_profile,
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "INPUT_TYPE_MISMATCH"]

    assert len(errors) == 1
    assert errors[0].step_id == "origin"
    assert errors[0].field == "inputs.city"
    assert "Number" in errors[0].message
    assert "Text" in errors[0].message


def test_plan_validator_rejects_incompatible_literal_type() -> None:
    """Reject literal values incompatible with capability input types."""
    registry = CapabilityRegistry()
    registry.register(get_geocode_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={
                    "city": LiteralValue(
                        kind="literal",
                        value=123,
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Germany",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "INPUT_TYPE_MISMATCH"]

    assert len(errors) == 1
    assert errors[0].step_id == "origin"
    assert errors[0].field == "inputs.city"
    assert "Number" in errors[0].message
    assert "Text" in errors[0].message


def test_plan_validator_rejects_unsupported_literal_type() -> None:
    """Reject literal types unsupported by the capability type system."""
    registry = CapabilityRegistry()
    registry.register(get_geocode_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={
                    "city": LiteralValue(
                        kind="literal",
                        value=True,
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Germany",
                    ),
                },
                parameters={},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "UNSUPPORTED_INPUT_TYPE"]

    assert len(errors) == 1
    assert errors[0].step_id == "origin"
    assert errors[0].field == "inputs.city"
    assert result.valid is False


def test_plan_validator_rejects_unknown_column_type() -> None:
    """Reject column inputs whose type cannot be determined."""
    registry = CapabilityRegistry()
    registry.register(get_geocode_spec())

    dataset_profile = DatasetProfile(
        dataset_id="test",
        name="Test dataset",
        columns=[
            ColumnProfile(
                name="city",
                inferred_type=PrimitiveType.UNKNOWN,
                nullable=True,
                null_count=0,
                sample_values=[],
                warnings=[],
            ),
        ],
    )

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="origin",
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
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=dataset_profile,
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "UNRESOLVED_INPUT_TYPE"]

    assert len(errors) == 1
    assert errors[0].step_id == "origin"
    assert errors[0].field == "inputs.city"
    assert result.valid is False


def test_plan_validator_rejects_unknown_parameter() -> None:
    """Reject parameters not declared by the capability."""
    registry = CapabilityRegistry()
    registry.register(get_geocode_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={
                    "city": LiteralValue(kind="literal", value="Berlin"),
                    "country": LiteralValue(kind="literal", value="Germany"),
                },
                parameters={"unknown_param": "value"},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "UNKNOWN_PARAMETER"]

    assert len(errors) == 1
    assert errors[0].step_id == "origin"
    assert errors[0].field == "parameters.unknown_param"


@pytest.mark.parametrize(
    ("parameter_value", "expected_code"),
    [
        ("city", "INVALID_PARAMETER_VALUE"),
        (123, "PARAMETER_TYPE_MISMATCH"),
        ("mountain", None),
    ],
)
def test_plan_validator_validates_parameter_values(
    parameter_value: str | int,
    expected_code: str | None,
) -> None:
    """Accept valid parameters and reject invalid values or types."""
    registry = CapabilityRegistry()
    registry.register(get_resolve_entity_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": LiteralValue(
                        kind="literal",
                        value="Everest",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Nepal",
                    ),
                },
                parameters={"entity_type": parameter_value},
            ),
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    if expected_code is None:
        assert result.valid is True
        assert result.errors == []
    else:
        errors = [error for error in result.errors if error.code == expected_code]

        assert len(errors) == 1
        assert errors[0].step_id == "resolve"
        assert errors[0].field == "parameters.entity_type"
        assert result.valid is False


def test_plan_validator_rejects_missing_output_step() -> None:
    """Reject output mappings referencing nonexistent steps."""
    plan = EnrichmentPlan(
        version="1.0",
        steps=[],
        outputs=[
            OutputMapping(
                step_id="missing_step",
                value_field="value",
                target_column="coordinates",
            ),
        ],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=CapabilityRegistry(),
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "MISSING_STEP_REFERENCE"]

    assert len(errors) == 1
    assert errors[0].field == "outputs[0].step_id"
    assert result.valid is False


@pytest.mark.parametrize(
    ("value_field", "expected_code"),
    [
        ("latitude", "UNKNOWN_OUTPUT_FIELD"),
        ("value", None),
        ("unit", None),
    ],
)
def test_plan_validator_validates_output_fields(
    value_field: str,
    expected_code: str | None,
) -> None:
    """Validate output fields against the capability contract."""
    registry = CapabilityRegistry()
    registry.register(get_resolve_entity_spec())
    registry.register(get_lookup_property_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": LiteralValue(kind="literal", value="Everest"),
                    "country": LiteralValue(kind="literal", value="Nepal"),
                },
                parameters={"entity_type": "mountain"},
            ),
            PlanStep(
                id="elevation",
                capability="lookup_property",
                inputs={
                    "entity": StepOutputReference(
                        kind="step_output",
                        step_id="resolve",
                    ),
                },
                parameters={"property": "elevation"},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="elevation",
                value_field=value_field,
                target_column="height",
            ),
        ],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    if expected_code is None:
        assert result.valid is True
        assert result.errors == []
    else:
        errors = [error for error in result.errors if error.code == expected_code]

        assert len(errors) == 1
        assert errors[0].step_id == "elevation"
        assert errors[0].field == "outputs[0].value_field"
        assert result.valid is False


def test_plan_validator_rejects_duplicate_output_target() -> None:
    """Reject multiple output mappings targeting the same column."""
    registry = CapabilityRegistry()
    registry.register(get_resolve_entity_spec())
    registry.register(get_lookup_property_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": LiteralValue(kind="literal", value="Everest"),
                    "country": LiteralValue(kind="literal", value="Nepal"),
                },
                parameters={"entity_type": "mountain"},
            ),
            PlanStep(
                id="elevation",
                capability="lookup_property",
                inputs={
                    "entity": StepOutputReference(
                        kind="step_output",
                        step_id="resolve",
                    ),
                },
                parameters={"property": "elevation"},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="elevation",
                value_field="value",
                target_column="height",
            ),
            OutputMapping(
                step_id="elevation",
                value_field="unit",
                target_column="height",
            ),
        ],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
    )

    result = validator.validate(plan)

    errors = [error for error in result.errors if error.code == "DUPLICATE_OUTPUT_TARGET"]

    assert len(errors) == 1
    assert errors[0].step_id == "elevation"
    assert errors[0].field == "outputs[1].target_column"
    assert result.valid is False


@pytest.mark.parametrize(
    ("protected_columns", "expected_code"),
    [
        ({"height"}, "PROTECTED_OUTPUT_TARGET"),
        ({"Mountain"}, None),
    ],
)
def test_plan_validator_validates_protected_output_target(
    protected_columns: set[str],
    expected_code: str | None,
) -> None:
    """Reject writes to protected columns and allow other targets."""

    registry = CapabilityRegistry()
    registry.register(get_resolve_entity_spec())
    registry.register(get_lookup_property_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": LiteralValue(kind="literal", value="Everest"),
                    "country": LiteralValue(kind="literal", value="Nepal"),
                },
                parameters={"entity_type": "mountain"},
            ),
            PlanStep(
                id="elevation",
                capability="lookup_property",
                inputs={
                    "entity": StepOutputReference(
                        kind="step_output",
                        step_id="resolve",
                    ),
                },
                parameters={"property": "elevation"},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="elevation",
                value_field="value",
                target_column="height",
            ),
        ],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
        policy=PlanValidationPolicy(
            protected_columns=frozenset(protected_columns),
        ),
    )

    result = validator.validate(plan)

    if expected_code is None:
        assert result.valid is True
        assert result.errors == []
    else:
        assert len(result.errors) == 1
        assert result.errors[0].code == expected_code
        assert result.errors[0].step_id == "elevation"
        assert result.errors[0].field == "outputs[0].target_column"
        assert result.valid is False


@pytest.mark.parametrize(
    ("max_steps", "expected_code"),
    [
        (1, "STEP_LIMIT_EXCEEDED"),
        (2, None),
        (None, None),
    ],
)
def test_plan_validator_validates_step_limit(
    max_steps: int | None,
    expected_code: str | None,
) -> None:
    """Validate step count against the configured limit."""
    registry = CapabilityRegistry()
    registry.register(get_geocode_spec())

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id=f"geocode_{index}",
                capability="geocode",
                inputs={
                    "city": LiteralValue(
                        kind="literal",
                        value="Berlin",
                    ),
                    "country": LiteralValue(
                        kind="literal",
                        value="Germany",
                    ),
                },
                parameters={},
            )
            for index in range(2)
        ],
        outputs=[],
        assumptions=[],
    )

    validator = PlanValidator(
        registry=registry,
        dataset_profile=DatasetProfile(
            dataset_id="test",
            name="Test dataset",
        ),
        policy=PlanValidationPolicy(max_steps=max_steps),
    )

    result = validator.validate(plan)

    if expected_code is None:
        assert result.valid is True
        assert result.errors == []
    else:
        assert len(result.errors) == 1
        assert result.errors[0].code == expected_code
        assert result.errors[0].field == "steps"
        assert result.valid is False
