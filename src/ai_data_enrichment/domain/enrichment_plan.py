"""Domain contracts for declarative enrichment plans."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from ai_data_enrichment.domain.capability import Identifier


class PlanContract(BaseModel):
    """Base model for strict enrichment plan contracts."""

    # Do not silently discard unexpected fields in generated plans.
    model_config = ConfigDict(extra="forbid")


class ColumnReference(PlanContract):
    """Reference a column in the input dataset."""

    kind: Literal["column"]
    name: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Reject column names containing only whitespace."""
        if not value.strip():
            raise ValueError("Column name must not be empty")
        return value


class StepOutputReference(PlanContract):
    """Reference the output of another plan step."""

    kind: Literal["step_output"]
    step_id: Identifier


class LiteralValue(PlanContract):
    """Represent a JSON-compatible constant used as a plan input."""

    kind: Literal["literal"]
    value: JsonValue


# The discriminator selects one reference shape without ambiguous union parsing.
InputReference = Annotated[
    ColumnReference | StepOutputReference | LiteralValue,
    Field(discriminator="kind"),
]


class PlanStep(PlanContract):
    """Describe a single capability invocation in an enrichment plan."""

    id: Identifier
    capability: Identifier
    inputs: dict[Identifier, InputReference]
    parameters: dict[Identifier, JsonValue]


class OutputMapping(PlanContract):
    """Map a step output field to a target dataset column."""

    step_id: Identifier
    value_field: Identifier
    target_column: str = Field(min_length=1)

    @field_validator("target_column")
    @classmethod
    def validate_target_column(cls, value: str) -> str:
        """Reject target columns containing only whitespace."""
        if not value.strip():
            raise ValueError("Target column must not be empty")
        return value


class EnrichmentPlan(PlanContract):
    """Describe a versioned, source-independent enrichment plan."""

    version: str = Field(min_length=1)
    steps: list[PlanStep]
    outputs: list[OutputMapping]
    assumptions: list[str]

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        """Reject versions containing only whitespace."""
        if not value.strip():
            raise ValueError("Version must not be empty")
        return value
