"""Unit tests for declarative enrichment plan contracts."""

import pytest
from pydantic import BaseModel, JsonValue, ValidationError

from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    InputReference,
    LiteralValue,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)


def test_column_reference_round_trip() -> None:
    """Column references should preserve their discriminator and name."""
    data = {
        "kind": "column",
        "name": "Mountain",
    }

    reference = ColumnReference.model_validate(data)

    assert reference.model_dump(mode="json") == data


def test_column_reference_rejects_whitespace_only_name() -> None:
    """Column references should reject whitespace-only names."""
    data = {
        "kind": "column",
        "name": "   ",
    }

    with pytest.raises(ValidationError):
        ColumnReference.model_validate(data)


def test_step_output_reference_round_trip() -> None:
    """Step output references should preserve their discriminator and step ID."""
    data = {
        "kind": "step_output",
        "step_id": "resolve_mountain",
    }

    reference = StepOutputReference.model_validate(data)

    assert reference.model_dump(mode="json") == data


@pytest.mark.parametrize(
    "step_id",
    [
        "",
        "ResolveMountain",
        "resolve-mountain",
        "_resolve_mountain",
        "resolve__mountain",
        "resolve_mountain_",
    ],
)
def test_step_output_reference_rejects_invalid_step_id(
    step_id: str,
) -> None:
    """Step references should reject invalid identifiers."""
    with pytest.raises(ValidationError) as exc_info:
        StepOutputReference.model_validate(
            {
                "kind": "step_output",
                "step_id": step_id,
            }
        )

    assert exc_info.value.errors()[0]["loc"] == ("step_id",)


def test_literal_value_round_trip() -> None:
    """Literal values should preserve their JSON representation."""
    data = {
        "kind": "literal",
        "value": "Germany",
    }

    reference = LiteralValue.model_validate(data)

    assert reference.model_dump(mode="json") == data


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        42,
        3.14,
        "Germany",
        ["Berlin", "Hamburg"],
        {"city": "Berlin", "country": "Germany"},
    ],
)
def test_literal_value_accepts_json_values(value: JsonValue) -> None:
    """Literal references should preserve JSON-compatible values."""
    data = {
        "kind": "literal",
        "value": value,
    }

    reference = LiteralValue.model_validate(data)

    assert reference.value == value
    assert reference.model_dump(mode="json") == data


def test_literal_value_requires_value() -> None:
    """Literal references must explicitly declare a value."""
    data = {
        "kind": "literal",
    }

    with pytest.raises(ValidationError) as exc_info:
        LiteralValue.model_validate(data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == ("value",)
    assert errors[0]["type"] == "missing"


def test_input_reference_selects_model_by_kind() -> None:
    """Input references should be parsed according to their kind."""

    class ReferenceHolder(BaseModel):
        reference: InputReference

    column = ReferenceHolder.model_validate(
        {"reference": {"kind": "column", "name": "Mountain"}}
    ).reference

    step = ReferenceHolder.model_validate(
        {"reference": {"kind": "step_output", "step_id": "resolve_mountain"}}
    ).reference

    literal = ReferenceHolder.model_validate(
        {"reference": {"kind": "literal", "value": "Germany"}}
    ).reference

    assert isinstance(column, ColumnReference)
    assert isinstance(step, StepOutputReference)
    assert isinstance(literal, LiteralValue)


def test_input_reference_rejects_unknown_kind() -> None:
    """Input references should reject unknown discriminator values."""

    class ReferenceHolder(BaseModel):
        reference: InputReference

    data = {
        "reference": {
            "kind": "unknown",
            "name": "Mountain",
        }
    }

    with pytest.raises(ValidationError) as exc_info:
        ReferenceHolder.model_validate(data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == ("reference",)
    assert errors[0]["type"] == "union_tag_invalid"


def test_plan_step_round_trip() -> None:
    """Plan steps should preserve typed inputs and parameters."""
    data = {
        "id": "resolve_mountain",
        "capability": "resolve_entity",
        "inputs": {
            "name": {"kind": "column", "name": "Mountain"},
            "country": {"kind": "column", "name": "Country"},
        },
        "parameters": {
            "entity_type": "mountain",
        },
    }

    step = PlanStep.model_validate(data)

    assert isinstance(step.inputs["name"], ColumnReference)
    assert isinstance(step.inputs["country"], ColumnReference)
    assert step.model_dump(mode="json") == data


def test_plan_step_accepts_step_output_reference() -> None:
    """Plan steps should accept references to other step outputs."""
    data = {
        "id": "retrieve_elevation",
        "capability": "lookup_property",
        "inputs": {
            "entity": {
                "kind": "step_output",
                "step_id": "resolve_mountain",
            },
        },
        "parameters": {
            "property": "elevation",
        },
    }

    step = PlanStep.model_validate(data)

    assert isinstance(step.inputs["entity"], StepOutputReference)
    assert step.inputs["entity"].step_id == "resolve_mountain"
    assert step.model_dump(mode="json") == data


@pytest.mark.parametrize(
    "missing_field",
    ["id", "capability", "inputs", "parameters"],
)
def test_plan_step_requires_all_fields(missing_field: str) -> None:
    """Plan steps should reject missing required fields."""
    data: dict[str, object] = {
        "id": "resolve_mountain",
        "capability": "resolve_entity",
        "inputs": {
            "name": {"kind": "column", "name": "Mountain"},
        },
        "parameters": {
            "entity_type": "mountain",
        },
    }

    data.pop(missing_field)

    with pytest.raises(ValidationError) as exc_info:
        PlanStep.model_validate(data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == (missing_field,)
    assert errors[0]["type"] == "missing"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("id", "ResolveMountain"),
        ("capability", "resolve-entity"),
    ],
)
def test_plan_step_rejects_invalid_identifiers(
    field_name: str,
    invalid_value: str,
) -> None:
    """Plan steps should reject invalid identifiers."""
    data: dict[str, object] = {
        "id": "resolve_mountain",
        "capability": "resolve_entity",
        "inputs": {},
        "parameters": {},
    }

    data[field_name] = invalid_value

    with pytest.raises(ValidationError) as exc_info:
        PlanStep.model_validate(data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == (field_name,)


@pytest.mark.parametrize(
    ("field_name", "invalid_key", "value"),
    [
        (
            "inputs",
            "Mountain-Name",
            {"kind": "column", "name": "Mountain"},
        ),
        (
            "parameters",
            "entity-type",
            "mountain",
        ),
    ],
)
def test_plan_step_rejects_invalid_argument_names(
    field_name: str,
    invalid_key: str,
    value: object,
) -> None:
    """Plan steps should reject invalid input and parameter names."""
    data: dict[str, object] = {
        "id": "resolve_mountain",
        "capability": "resolve_entity",
        "inputs": {},
        "parameters": {},
    }

    data[field_name] = {invalid_key: value}

    with pytest.raises(ValidationError) as exc_info:
        PlanStep.model_validate(data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"][:2] == (field_name, invalid_key)


def test_output_mapping_round_trip() -> None:
    """Output mappings should preserve source and target fields."""
    data = {
        "step_id": "retrieve_elevation",
        "value_field": "value",
        "target_column": "height",
    }

    mapping = OutputMapping.model_validate(data)

    assert mapping.model_dump(mode="json") == data


def test_output_mapping_rejects_whitespace_only_target_column() -> None:
    """Output mappings should reject whitespace-only target columns."""
    data = {
        "step_id": "retrieve_elevation",
        "value_field": "value",
        "target_column": "   ",
    }

    with pytest.raises(ValidationError) as exc_info:
        OutputMapping.model_validate(data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == ("target_column",)


@pytest.mark.parametrize(
    "missing_field",
    ["step_id", "value_field", "target_column"],
)
def test_output_mapping_requires_all_fields(missing_field: str) -> None:
    """Output mappings should reject missing required fields."""
    data: dict[str, object] = {
        "step_id": "retrieve_elevation",
        "value_field": "value",
        "target_column": "height",
    }

    data.pop(missing_field)

    with pytest.raises(ValidationError) as exc_info:
        OutputMapping.model_validate(data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == (missing_field,)
    assert errors[0]["type"] == "missing"


def test_enrichment_plan_round_trip() -> None:
    """Enrichment plans should preserve nested steps and output mappings."""
    data = {
        "version": "1.0",
        "steps": [
            {
                "id": "retrieve_elevation",
                "capability": "lookup_property",
                "inputs": {
                    "entity": {
                        "kind": "step_output",
                        "step_id": "resolve_mountain",
                    }
                },
                "parameters": {
                    "property": "elevation",
                },
            }
        ],
        "outputs": [
            {
                "step_id": "retrieve_elevation",
                "value_field": "value",
                "target_column": "height",
            }
        ],
        "assumptions": [
            "Elevation is expressed in meters.",
        ],
    }

    plan = EnrichmentPlan.model_validate(data)

    assert isinstance(plan.steps[0], PlanStep)
    assert isinstance(plan.outputs[0], OutputMapping)
    assert isinstance(plan.steps[0].inputs["entity"], StepOutputReference)
    assert plan.model_dump(mode="json") == data


@pytest.mark.parametrize(
    "missing_field",
    ["version", "steps", "outputs", "assumptions"],
)
def test_enrichment_plan_requires_all_fields(missing_field: str) -> None:
    """Enrichment plans should reject missing required fields."""
    data: dict[str, object] = {
        "version": "1.0",
        "steps": [],
        "outputs": [],
        "assumptions": [],
    }

    data.pop(missing_field)

    with pytest.raises(ValidationError) as exc_info:
        EnrichmentPlan.model_validate(data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == (missing_field,)
    assert errors[0]["type"] == "missing"


def test_enrichment_plan_rejects_invalid_nested_step() -> None:
    """Enrichment plans should reject invalid nested steps."""
    data = {
        "version": "1.0",
        "steps": [
            {
                "id": "retrieve_elevation",
                "capability": "lookup-property",
                "inputs": {},
                "parameters": {},
            }
        ],
        "outputs": [],
        "assumptions": [],
    }

    with pytest.raises(ValidationError) as exc_info:
        EnrichmentPlan.model_validate(data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == ("steps", 0, "capability")
    assert errors[0]["type"] == "string_pattern_mismatch"


def test_mountains_plan_json_round_trip() -> None:
    """Mountain enrichment plans should survive JSON serialization."""
    data = {
        "version": "1.0",
        "steps": [
            {
                "id": "resolve_mountain",
                "capability": "resolve_entity",
                "inputs": {
                    "name": {"kind": "column", "name": "Mountain"},
                    "country": {"kind": "column", "name": "Country"},
                },
                "parameters": {
                    "entity_type": "mountain",
                },
            },
            {
                "id": "retrieve_elevation",
                "capability": "lookup_property",
                "inputs": {
                    "entity": {
                        "kind": "step_output",
                        "step_id": "resolve_mountain",
                    },
                },
                "parameters": {
                    "property": "elevation",
                },
            },
        ],
        "outputs": [
            {
                "step_id": "retrieve_elevation",
                "value_field": "value",
                "target_column": "height",
            }
        ],
        "assumptions": [
            "Elevation is expressed in meters.",
        ],
    }

    plan = EnrichmentPlan.model_validate(data)

    serialized = plan.model_dump_json()
    restored = EnrichmentPlan.model_validate_json(serialized)

    assert restored == plan
    assert restored.model_dump(mode="json") == data
    assert isinstance(
        restored.steps[1].inputs["entity"],
        StepOutputReference,
    )


def test_capitals_plan_json_round_trip() -> None:
    """Capital distance plans should survive JSON serialization."""
    data = {
        "version": "1.0",
        "steps": [
            {
                "id": "origin",
                "capability": "geocode",
                "inputs": {
                    "city": {"kind": "column", "name": "Capital_From"},
                    "country": {"kind": "column", "name": "Country_From"},
                },
                "parameters": {},
            },
            {
                "id": "destination",
                "capability": "geocode",
                "inputs": {
                    "city": {"kind": "column", "name": "Capital_To"},
                    "country": {"kind": "column", "name": "Country_To"},
                },
                "parameters": {},
            },
            {
                "id": "distance",
                "capability": "geodesic_distance",
                "inputs": {
                    "point_a": {
                        "kind": "step_output",
                        "step_id": "origin",
                    },
                    "point_b": {
                        "kind": "step_output",
                        "step_id": "destination",
                    },
                },
                "parameters": {
                    "unit": "km",
                },
            },
        ],
        "outputs": [
            {
                "step_id": "distance",
                "value_field": "value",
                "target_column": "distance",
            }
        ],
        "assumptions": [
            "Straight-line distance means geodesic distance, not driving distance.",
            "Distances are calculated between resolved city reference coordinates.",
        ],
    }

    plan = EnrichmentPlan.model_validate(data)

    serialized = plan.model_dump_json()
    restored = EnrichmentPlan.model_validate_json(serialized)

    assert restored == plan
    assert restored.model_dump(mode="json") == data

    distance_step = restored.steps[2]

    assert isinstance(
        distance_step.inputs["point_a"],
        StepOutputReference,
    )
    assert isinstance(
        distance_step.inputs["point_b"],
        StepOutputReference,
    )


def test_enrichment_plan_rejects_whitespace_only_version() -> None:
    """Enrichment plans should reject whitespace-only versions."""
    data = {
        "version": "   ",
        "steps": [],
        "outputs": [],
        "assumptions": [],
    }

    with pytest.raises(ValidationError) as exc_info:
        EnrichmentPlan.model_validate(data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == ("version",)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("step_id", "RetrieveElevation"),
        ("value_field", "value-field"),
    ],
)
def test_output_mapping_rejects_invalid_identifiers(
    field_name: str,
    invalid_value: str,
) -> None:
    """Output mappings should reject invalid identifiers."""
    data = {
        "step_id": "retrieve_elevation",
        "value_field": "value",
        "target_column": "height",
    }

    data[field_name] = invalid_value

    with pytest.raises(ValidationError) as exc_info:
        OutputMapping.model_validate(data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == (field_name,)
    assert errors[0]["type"] == "string_pattern_mismatch"