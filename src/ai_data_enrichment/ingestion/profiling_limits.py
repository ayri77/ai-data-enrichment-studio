"""Configuration for dataset profiling limits."""

from pydantic import BaseModel, Field


class ProfilingLimits(BaseModel):
    """Configuration limits for dataset profiling."""

    max_sample_records: int = Field(default=5, ge=1)
    max_value_length: int = Field(default=200, ge=1)
    max_profile_bytes: int = Field(default=12_000, ge=1)
