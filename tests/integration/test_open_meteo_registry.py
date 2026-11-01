"""Integration tests for approved geocoding providers."""

from ai_data_enrichment.capabilities.definitions import (
    get_geocode_spec,
    get_geodesic_distance_spec,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.http_transport import UrllibJsonTransport
from ai_data_enrichment.providers.open_meteo import OpenMeteoGeocodingHandler
from ai_data_enrichment.providers.registry import ProviderRegistry


def test_registers_and_resolves_real_providers() -> None:
    """Resolve explicit provider choices to their real implementations."""
    capabilities = CapabilityRegistry()
    capabilities.register(get_geocode_spec())
    capabilities.register(get_geodesic_distance_spec())

    providers = ProviderRegistry(capabilities)

    geocoder = OpenMeteoGeocodingHandler(transport=UrllibJsonTransport())
    distance = GeodesicDistanceHandler()

    providers.register(
        capability_id="geocode",
        provider_id="open_meteo",
        handler=geocoder,
    )
    providers.register(
        capability_id="geodesic_distance",
        provider_id="haversine",
        handler=distance,
    )

    handlers = providers.resolve_handlers(
        capability_ids=["geocode", "geodesic_distance"],
        selected_providers={
            "geocode": "open_meteo",
            "geodesic_distance": "haversine",
        },
    )

    assert handlers["geocode"] is geocoder
    assert handlers["geodesic_distance"] is distance
