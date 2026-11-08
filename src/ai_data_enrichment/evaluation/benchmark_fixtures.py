"""Validated offline geocoding fixtures for reproducible benchmarks."""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GeocodingQuery(BaseModel):
    """Parameters identifying a geocoding request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    countryCode: str = Field(
        min_length=2,
        max_length=2,
        pattern=r"^[A-Z]{2}$",
    )


class GeocodingCandidate(BaseModel):
    """A geocoding candidate returned by the offline provider."""

    model_config = ConfigDict(extra="allow", frozen=True)

    id: int = Field(gt=0, strict=True)
    name: str = Field(min_length=1)
    country_code: str = Field(
        min_length=2,
        max_length=2,
        pattern=r"^[A-Z]{2}$",
    )
    feature_code: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class GeocodingPayload(BaseModel):
    """A deterministic provider response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    results: tuple[GeocodingCandidate, ...] = Field(min_length=1)


class GeocodingFixtureResponse(BaseModel):
    """Maps one request to its recorded provider response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: GeocodingQuery
    payload: GeocodingPayload


class GeocodingFixture(BaseModel):
    """Collection of validated offline provider responses."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    provider: Literal["open-meteo"]
    description: str = Field(min_length=1)
    responses: tuple[GeocodingFixtureResponse, ...] = Field(
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_unique_queries(self) -> Self:
        seen: set[tuple[str, str]] = set()

        for response in self.responses:
            key = (
                response.query.name,
                response.query.countryCode,
            )

            if key in seen:
                raise ValueError(f"Duplicate geocoding fixture query: {key}")

            seen.add(key)

        return self

    def get_response(
        self,
        name: str,
        country_code: str,
    ) -> dict[str, object]:
        """Return a provider payload for an exact query."""

        for response in self.responses:
            if response.query.name == name and response.query.countryCode == country_code:
                return response.payload.model_dump(mode="json")

        raise ValueError(
            f"No offline geocoding fixture for name={name!r}, countryCode={country_code!r}"
        )


def load_geocoding_fixture(path: Path) -> GeocodingFixture:
    """Load and validate an offline geocoding fixture."""

    return GeocodingFixture.model_validate_json(path.read_text(encoding="utf-8"))
