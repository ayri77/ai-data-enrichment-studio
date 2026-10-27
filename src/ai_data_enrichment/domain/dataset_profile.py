"""Domain objects for dataset profiling."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class PrimitiveType(StrEnum):
    """Canonical primitive types used for dataset profiling."""

    TEXT = "Text"
    INTEGER = "Integer"
    NUMBER = "Number"
    BOOLEAN = "Boolean"
    DATE = "Date"
    DATETIME = "DateTime"
    UNKNOWN = "Unknown"
    MIXED = "Mixed"


class ColumnProfile(BaseModel):
    """Profile of a single column in a dataset."""

    model_config = ConfigDict(allow_inf_nan=False)

    name: str
    source_type: str | None = None
    inferred_type: PrimitiveType
    nullable: bool | None = None
    null_count: int = Field(ge=0)
    sample_values: list[JsonValue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Reject empty or whitespace-only column names."""
        if not value.strip():
            raise ValueError("Column name must not be empty")
        return value


class DatasetProfile(BaseModel):
    """Profile of a dataset."""

    model_config = ConfigDict(allow_inf_nan=False)

    dataset_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    row_count: int | None = Field(default=None, ge=0)
    columns: list[ColumnProfile] = Field(default_factory=list)
    sample_records: list[dict[str, JsonValue]] = Field(default_factory=list)
    schema_warnings: list[str] = Field(default_factory=list)

    @field_validator("dataset_id")
    @classmethod
    def validate_dataset_id(cls, value: str) -> str:
        """Reject empty or whitespace-only dataset IDs."""
        if not value.strip():
            raise ValueError("Dataset ID must not be empty")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Reject empty or whitespace-only dataset names."""
        if not value.strip():
            raise ValueError("Dataset name must not be empty")
        return value
