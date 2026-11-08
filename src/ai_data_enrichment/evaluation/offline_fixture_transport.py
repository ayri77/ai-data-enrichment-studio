"""Deterministic offline transport backed by validated fixtures."""

from collections.abc import Mapping

from ai_data_enrichment.evaluation.benchmark_fixtures import (
    GeocodingFixture,
)
from ai_data_enrichment.providers.open_meteo import GEOCODING_URL


class OfflineFixtureTransport:
    """Emulate Open-Meteo geocoding without network requests."""

    def __init__(self, fixture: GeocodingFixture) -> None:
        self.fixture = fixture
        self.calls = 0
        self.requested_cities: list[str] = []
        self.requested_queries: list[tuple[str, str]] = []

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        """Return a validated fixture response for an exact query."""

        if url != GEOCODING_URL:
            raise ValueError(f"Unexpected provider URL: {url}")

        if timeout_seconds <= 0:
            raise ValueError("Timeout must be positive.")

        if params.get("count") != "100":
            raise ValueError("Offline fixture expects count=100.")

        city = params.get("name")
        country = params.get("countryCode")

        if not city or not country:
            raise ValueError("Geocoding request requires name and countryCode.")

        # Resolve before recording a successful fixture lookup.
        payload = self.fixture.get_response(city, country)

        self.calls += 1
        self.requested_cities.append(city)
        self.requested_queries.append((city, country))

        return payload
