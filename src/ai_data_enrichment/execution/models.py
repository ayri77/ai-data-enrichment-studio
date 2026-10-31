"""Typed runtime values and execution results."""

from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    field_validator,
    model_validator,
)

from ai_data_enrichment.domain.capability import Identifier


class GeoPoint(BaseModel):
    """Represent a geographic point with validated coordinates."""

    model_config = ConfigDict(frozen=True)

    # Latitude is measured north/south of the equator.
    latitude: StrictFloat = Field(
        ge=-90.0,
        le=90.0,
        allow_inf_nan=False,
    )

    # Longitude is measured east/west of the prime meridian.
    longitude: StrictFloat = Field(
        ge=-180.0,
        le=180.0,
        allow_inf_nan=False,
    )


class EntityRef(BaseModel):
    """Identify an entity within a specific external data source."""

    model_config = ConfigDict(frozen=True)

    # Entity IDs are source-specific and cannot identify an entity alone.
    source: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)

    @field_validator("source", "entity_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        """Reject identifiers containing only whitespace."""
        if not value.strip():
            raise ValueError("Entity identity fields must not be empty")
        return value


class Quantity(BaseModel):
    """Represent a finite numeric measurement with an explicit unit."""

    model_config = ConfigDict(frozen=True)

    # Reject NaN and Infinity before values reach calculations or Excel output.
    value: StrictFloat = Field(allow_inf_nan=False)

    # Units are explicit but not restricted to a fixed list of dimensions.
    unit: str = Field(min_length=1)

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str) -> str:
        """Reject missing or whitespace-only measurement units."""
        if not value.strip():
            raise ValueError("Measurement unit must not be empty")
        return value


class Evidence(BaseModel):
    """Identify the source supporting an operation result."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    reference: str = Field(min_length=1)

    @field_validator("source", "reference")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        """Reject empty or whitespace-only source references."""
        if not value.strip():
            raise ValueError("Evidence fields must not be empty")
        return value


# All value types currently supported by the execution layer.
RuntimeValue = GeoPoint | EntityRef | Quantity | str | StrictFloat


class OperationResult(BaseModel):
    """Represent either a successful operation or an explicit failure."""

    model_config = ConfigDict(frozen=True)

    status: Literal["success", "failure"]
    value: RuntimeValue | None = None
    error_code: str | None = None
    message: str | None = None
    # Evidence is immutable to preserve the validated result state.
    evidence: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        """Ensure success and failure states cannot contradict each other."""

        if self.status == "success":
            # A successful enrichment operation must produce a value.
            if self.value is None:
                raise ValueError("Successful operation must contain a value")

            if self.error_code is not None or self.message is not None:
                raise ValueError("Successful operation cannot contain error details")

        else:
            # Failures must not expose partial data as a successful result.
            if self.value is not None:
                raise ValueError("Failed operation cannot contain a value")

            if not self.error_code or not self.error_code.strip():
                raise ValueError("Failed operation must contain an error code")

            if not self.message or not self.message.strip():
                raise ValueError("Failed operation must contain an error message")

        return self


class StepExecutionResult(BaseModel):
    """Associate a plan step with its execution outcome."""

    model_config = ConfigDict(frozen=True)

    step_id: Identifier
    result: OperationResult


class RowExecutionResult(BaseModel):
    """Preserve the ordered execution outcomes for one input row."""

    model_config = ConfigDict(frozen=True)

    # Tuple preserves execution order and prevents in-place modification.
    steps: tuple[StepExecutionResult, ...] = ()

    @model_validator(mode="after")
    def validate_unique_steps(self) -> Self:
        """Reject repeated execution records for the same DAG step."""

        step_ids = [step.step_id for step in self.steps]

        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Step IDs must be unique within a row")

        return self
