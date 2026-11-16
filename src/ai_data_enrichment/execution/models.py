"""Typed runtime values and execution results."""

from math import isfinite
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
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

    # What the source records about the matched place, when it records it.
    # They ride along with the accepted record and never take part in matching;
    # None means the source gave no value, not that the value is zero.
    population: StrictInt | None = Field(default=None, ge=0)
    # An IANA name such as "Europe/Berlin".
    timezone: StrictStr | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Timezone must not be blank.")
        return value


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


class FoundValue(BaseModel):
    """One value a model found on the web for one target column, or "not found".

    The value is never checked against a record: ``basis`` says whether a page
    stated it or the model concluded it. ``value`` is None when nothing was found.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Text or a number (yes/no is written as text); None means "not found".
    value: StrictStr | StrictFloat | None
    # Required with a value, absent without one.
    basis: Literal["stated", "concluded"] | None
    # Links of the pages this value rests on, as the model reported them.
    sources: tuple[StrictStr, ...] = ()
    # A short passage of a page that carries the value, when there is one.
    quote: StrictStr | None = None
    unit: StrictStr | None = None

    @model_validator(mode="after")
    def validate_found_value(self) -> Self:
        """Tie the basis to the value and reject blank or non-finite content."""
        if self.value is None:
            if self.basis is not None:
                raise ValueError("A value that was not found cannot have a basis")
        else:
            if self.basis is None:
                raise ValueError("A found value must say whether it was stated or concluded")
            if isinstance(self.value, str) and not self.value.strip():
                raise ValueError("A text value must not be blank")
            if isinstance(self.value, float) and not isfinite(self.value):
                raise ValueError("A numeric value must be finite")
        if any(not link.strip() for link in self.sources):
            raise ValueError("Source links must not be blank")
        if self.quote is not None and not self.quote.strip():
            raise ValueError("A quote must not be blank")
        if self.unit is not None and not self.unit.strip():
            raise ValueError("A unit must not be blank")
        return self


class FoundRecord(BaseModel):
    """What one web search answered for one row: a ``FoundValue`` per slot.

    The slots are named ``value_1`` ... ``value_N``; the plan's output mapping
    decides which column each one fills.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slots: dict[Identifier, FoundValue] = Field(min_length=1)


# All value types currently supported by the execution layer.
RuntimeValue = GeoPoint | EntityRef | Quantity | FoundRecord | str | StrictFloat


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
