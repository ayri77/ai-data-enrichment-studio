"""Validated configuration for reproducible offline benchmarks."""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BenchmarkScenario(BaseModel):
    """Declarative benchmark workload configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)

    rows: int = Field(ge=1, le=1000, strict=True)

    dataset: Literal["capitals", "unique_cities"]
    unique_locations: int = Field(ge=1, strict=True)

    distribution: Literal["alternating", "round_robin"]

    resolution_strategy: Literal["strict"]
    required_role: Literal["country_capital"] | None

    memo_capacity: int = Field(ge=0, le=3000, strict=True)
    fixture: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9_-]+\.json$",
    )

    @model_validator(mode="after")
    def validate_workload(self) -> Self:
        if self.dataset == "capitals":
            if self.required_role != "country_capital":
                raise ValueError("Capitals scenario requires country_capital role.")
            if self.unique_locations != 2:
                raise ValueError("Current capitals fixture contains exactly two locations.")
            if self.distribution != "alternating":
                raise ValueError("Capitals scenario requires alternating distribution.")

        if self.dataset == "unique_cities":
            if self.required_role is not None:
                raise ValueError("Unique cities must use ordinary geocoding.")
            if self.distribution != "round_robin":
                raise ValueError("Unique cities require round_robin distribution.")

        return self


def load_scenario(path: Path) -> BenchmarkScenario:
    """Read and validate a scenario without executing providers."""

    return BenchmarkScenario.model_validate_json(path.read_text(encoding="utf-8"))
