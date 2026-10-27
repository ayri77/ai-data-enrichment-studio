"""Unit tests for capability domain contracts."""

import pytest
from pydantic import ValidationError

from ai_data_enrichment.domain.capability import (
    CapabilitySpec,
    DataType,
    InputSpec,
    OutputSpec,
    ParameterSpec,
)


@pytest.fixture
def valid_capability_data() -> dict[str, object]:
    """Provide valid capability data for validation tests."""
    return {
        "id": "geocode",
        "version": "1.0",
        "description": "Resolve geographic coordinates",
        "inputs": {},
        "output": {
            "type": "GeoPoint",
            "description": "Resolved geographical coordinates",
        },
    }


@pytest.mark.parametrize(
    ("model_class", "extra_fields", "expected_error"),
    [
        (InputSpec, {}, "enum"),
        (OutputSpec, {}, "enum"),
        (ParameterSpec, {"allowed_values": ["km"]}, "literal_error"),
    ],
)
def test_spec_rejects_invalid_type(
    model_class: type[InputSpec] | type[OutputSpec] | type[ParameterSpec],
    extra_fields: dict[str, list[str]],
    expected_error: str,
) -> None:
    """Capability specs should reject unsupported semantic data types."""
    with pytest.raises(ValidationError) as exc_info:
        model_class.model_validate(
            {
                "type": "Unknown",
                "description": "Invalid data type",
                **extra_fields,
            }
        )

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == ("type",)
    assert errors[0]["type"] == expected_error


@pytest.mark.parametrize(
    ("model_class", "extra_fields"),
    [
        (InputSpec, {}),
        (OutputSpec, {}),
        (ParameterSpec, {"allowed_values": ["km"]}),
    ],
)
def test_spec_requires_description(
    model_class: type[InputSpec] | type[OutputSpec] | type[ParameterSpec],
    extra_fields: dict[str, list[str]],
) -> None:
    """Capability specs should reject a missing description."""
    with pytest.raises(ValidationError) as exc_info:
        model_class.model_validate(
            {
                "type": "Text",
                **extra_fields,
            }
        )

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == ("description",)
    assert errors[0]["type"] == "missing"


@pytest.mark.parametrize(
    ("model_class", "extra_fields"),
    [
        (InputSpec, {}),
        (OutputSpec, {}),
        (ParameterSpec, {"allowed_values": ["km"]}),
    ],
)
def test_spec_rejects_empty_description(
    model_class: type[InputSpec] | type[OutputSpec] | type[ParameterSpec],
    extra_fields: dict[str, list[str]],
) -> None:
    """Capability specs should reject an empty description."""
    with pytest.raises(ValidationError) as exc_info:
        model_class.model_validate(
            {
                "type": "Text",
                "description": "",
                **extra_fields,
            }
        )

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("description",)
    assert errors[0]["type"] == "string_too_short"


def test_input_spec_creation() -> None:
    """InputSpec should accept valid data and apply default values."""
    spec = InputSpec(
        type=DataType.TEXT,
        description="City name to geocode",
    )

    assert spec.type == DataType.TEXT
    assert spec.required is True
    assert spec.description == "City name to geocode"


def test_output_spec_creation() -> None:
    """OutputSpec should accept valid output definitions."""
    spec = OutputSpec(
        type=DataType.GEO_POINT,
        description="Resolved geographical coordinates",
    )

    assert spec.type == DataType.GEO_POINT
    assert spec.description == "Resolved geographical coordinates"


def test_parameter_spec_creation() -> None:
    """ParameterSpec should accept a valid parameter definition."""
    spec = ParameterSpec(
        type=DataType.TEXT,
        description="Distance measurement unit",
        allowed_values=["km"],
    )

    assert spec.type == DataType.TEXT
    assert spec.description == "Distance measurement unit"
    assert spec.allowed_values == ["km"]


def test_parameter_spec_requires_allowed_values() -> None:
    """ParameterSpec should require a list of allowed values."""
    with pytest.raises(ValidationError) as exc_info:
        ParameterSpec.model_validate(
            {
                "type": "Text",
                "description": "Distance measurement unit",
            }
        )

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == ("allowed_values",)
    assert errors[0]["type"] == "missing"


def test_parameter_spec_rejects_empty_allowed_values() -> None:
    """ParameterSpec should reject an empty list of allowed values."""
    with pytest.raises(ValidationError) as exc_info:
        ParameterSpec.model_validate(
            {
                "type": "Text",
                "description": "Distance measurement unit",
                "allowed_values": [],
            }
        )

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == ("allowed_values",)
    assert errors[0]["type"] == "too_short"


def test_parameter_spec_rejects_non_text_type() -> None:
    """ParameterSpec should reject unsupported parameter types."""
    with pytest.raises(ValidationError) as exc_info:
        ParameterSpec.model_validate(
            {
                "type": "Number",
                "allowed_values": ["10", "20"],
                "description": "Numeric parameter",
            }
        )

    assert exc_info.value.errors()[0]["loc"] == ("type",)


def test_capability_spec_creation() -> None:
    """CapabilitySpec should accept a valid capability definition."""
    spec = CapabilitySpec(
        id="geocode",
        version="1.0",
        description="Resolve a city and country into geographic coordinates",
        inputs={
            "city": InputSpec(type=DataType.TEXT, description="City name"),
            "country": InputSpec(type=DataType.TEXT, description="Country name"),
        },
        output=OutputSpec(type=DataType.GEO_POINT, description="Resolved geographical coordinates"),
    )

    assert spec.id == "geocode"
    assert spec.version == "1.0"
    assert spec.description == "Resolve a city and country into geographic coordinates"

    assert set(spec.inputs) == {"city", "country"}
    assert spec.inputs["city"].type == DataType.TEXT
    assert spec.inputs["country"].type == DataType.TEXT
    assert spec.inputs["city"].required is True
    assert spec.inputs["country"].required is True

    assert spec.parameters == {}

    assert spec.output.type == DataType.GEO_POINT
    assert spec.output.description == "Resolved geographical coordinates"


def test_capability_spec_rejects_invalid_nested_input() -> None:
    """CapabilitySpec should reject an invalid nested input definition."""
    with pytest.raises(ValidationError) as exc_info:
        CapabilitySpec.model_validate(
            {
                "id": "geocode",
                "version": "1.0",
                "description": "Resolve a city and country into geographic coordinates",
                "inputs": {"city": {"type": "Unknown", "description": "City name"}},
                "output": {"type": "GeoPoint", "description": "Resolved geographical coordinates"},
            }
        )

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("inputs", "city", "type")
    assert errors[0]["type"] == "enum"


def test_capability_spec_rejects_invalid_nested_parameter() -> None:
    """CapabilitySpec should reject an invalid nested parameter definition."""
    with pytest.raises(ValidationError) as exc_info:
        CapabilitySpec.model_validate(
            {
                "id": "geodesic_distance",
                "version": "1.0",
                "description": "Calculate the geodesic distance between two points",
                "inputs": {
                    "point_a": {"type": "GeoPoint", "description": "First geographic point"},
                    "point_b": {"type": "GeoPoint", "description": "Second geographic point"},
                },
                "parameters": {
                    "unit": {
                        "type": "Text",
                        "description": "Distance measurement unit",
                        "allowed_values": [],
                    }
                },
                "output": {
                    "type": "Quantity",
                    "description": "Calculated distance",
                },
            }
        )

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("parameters", "unit", "allowed_values")
    assert errors[0]["type"] == "too_short"


@pytest.mark.parametrize("field_name", ["id", "version", "description"])
def test_capability_spec_rejects_empty_text_fields(
    field_name: str,
    valid_capability_data: dict[str, object],
) -> None:
    """CapabilitySpec should reject empty required text fields."""
    valid_capability_data[field_name] = ""

    with pytest.raises(ValidationError) as exc_info:
        CapabilitySpec.model_validate(valid_capability_data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == (field_name,)
    assert errors[0]["type"] == "string_too_short"


@pytest.mark.parametrize(
    "field_name",
    ["id", "version", "description", "inputs", "output"],
)
def test_capability_spec_requires_mandatory_fields(
    field_name: str,
    valid_capability_data: dict[str, object],
) -> None:
    """CapabilitySpec should reject missing mandatory fields."""
    valid_capability_data.pop(field_name)

    with pytest.raises(ValidationError) as exc_info:
        CapabilitySpec.model_validate(valid_capability_data)

    errors = exc_info.value.errors()

    assert len(errors) == 1
    assert errors[0]["loc"] == (field_name,)
    assert errors[0]["type"] == "missing"


@pytest.mark.parametrize(
    "invalid_id",
    [
        "   ",
        ",..,",
        "GeoCode",
        "resolve__entity",
        "_geocode",
        "geocode_",
        "1geocode",
        "a" * 65,
    ],
)
def test_capability_spec_rejects_invalid_id(invalid_id: str) -> None:
    """Capability IDs must follow the snake_case naming convention."""
    with pytest.raises(ValidationError) as exc_info:
        CapabilitySpec(
            id=invalid_id,
            version="1.0",
            description="Test capability",
            inputs={},
            output=OutputSpec(
                type=DataType.TEXT,
                description="Test output",
            ),
        )
    assert exc_info.value.errors()[0]["loc"] == ("id",)


@pytest.mark.parametrize(
    "valid_id",
    [
        "geocode",
        "resolve_entity",
        "geocode_v2",
        "a",
        "a" * 64,
    ],
)
def test_capability_spec_accepts_valid_id(valid_id: str) -> None:
    """Capability IDs should accept valid snake_case identifiers."""
    spec = CapabilitySpec(
        id=valid_id,
        version="1.0",
        description="Test capability",
        inputs={},
        output=OutputSpec(
            type=DataType.TEXT,
            description="Test output",
        ),
    )
    assert spec.id == valid_id


@pytest.mark.parametrize("field_name", ["inputs", "parameters"])
@pytest.mark.parametrize(
    "invalid_name",
    [
        "",
        "   ",
        "City Name",
        "GeoCode",
        "_name",
        "name_",
        "point__a",
        "1name",
        ",..,",
        "a" * 65,
    ],
)
def test_capability_spec_rejects_invalid_argument_names(
    field_name: str,
    invalid_name: str,
) -> None:
    """Input and parameter names must follow the snake_case convention."""
    payload: dict[str, object] = {
        "id": "test_capability",
        "version": "1.0",
        "description": "Test capability",
        "inputs": {},
        "parameters": {},
        "output": OutputSpec(
            type=DataType.TEXT,
            description="Test output",
        ),
    }

    if field_name == "inputs":
        payload["inputs"] = {
            invalid_name: InputSpec(
                type=DataType.TEXT,
                description="Test input",
            ),
        }
    else:
        payload["parameters"] = {
            invalid_name: ParameterSpec(
                type=DataType.TEXT,
                allowed_values=["test"],
                description="Test parameter",
            ),
        }

    with pytest.raises(ValidationError) as exc_info:
        CapabilitySpec.model_validate(payload)

    assert any(error["loc"][0] == field_name for error in exc_info.value.errors())


@pytest.mark.parametrize(
    "valid_name",
    [
        "name",
        "point_a",
        "entity_type2",
        "a",
        "a" * 64,
    ],
)
def test_capability_spec_accepts_valid_argument_names(
    valid_name: str,
) -> None:
    """Input and parameter names should accept valid snake_case identifiers."""
    spec = CapabilitySpec(
        id="test_capability",
        version="1.0",
        description="Test capability",
        inputs={
            valid_name: InputSpec(
                type=DataType.TEXT,
                description="Test input",
            ),
        },
        parameters={
            valid_name: ParameterSpec(
                type=DataType.TEXT,
                allowed_values=["test"],
                description="Test parameter",
            ),
        },
        output=OutputSpec(
            type=DataType.TEXT,
            description="Test output",
        ),
    )

    assert valid_name in spec.inputs
    assert valid_name in spec.parameters
