"""Domain contracts for registered enrichment capabilities."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
    ),
]


class DataType(StrEnum):
    """Supported semantic data types for capability contracts."""

    TEXT = "Text"
    NUMBER = "Number"
    QUANTITY = "Quantity"
    GEO_POINT = "GeoPoint"
    ENTITY_REF = "EntityRef"


class InputSpec(BaseModel):
    """Describe a single input argument accepted by a capability."""

    type: DataType = Field(description="Semantic type of the input value")
    required: bool = Field(default=True, description="Whether the argument is mandatory")
    description: str = Field(min_length=1, description="Human-readable description of the argument")


class OutputSpec(BaseModel):
    """Describe the primary output produced by a capability."""

    type: DataType = Field(description="Semantic type of the output value")
    description: str = Field(min_length=1, description="Human-readable description of the output")


class ParameterSpec(BaseModel):
    """Describe a configurable parameter accepted by a capability."""

    type: Literal[DataType.TEXT] = Field(description="Semantic type of the parameter")
    allowed_values: list[str] = Field(
        min_length=1,
        description="Permitted values for the parameter",
    )
    description: str = Field(
        min_length=1, description="Human-readable description of the parameter"
    )


class CapabilitySpec(BaseModel):
    """Describe a complete capability definition."""

    id: Identifier = Field(
        description="Unique capability identifier in snake_case",
    )
    version: str = Field(min_length=1, description="Version of the capability")
    description: str = Field(
        min_length=1, description="Human-readable description of the capability"
    )
    inputs: dict[Identifier, InputSpec] = Field(
        description="Input arguments accepted by the capability"
    )

    parameters: dict[Identifier, ParameterSpec] = Field(
        default_factory=dict,
        description="Configurable parameters accepted by the capability",
    )
    output: OutputSpec = Field(description="Primary output produced by the capability")
