"""Configure approved live providers for Excel enrichment."""

from collections.abc import Mapping
from typing import Literal

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import JobExecutionPolicy
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.http_transport import JsonTransport, UrllibJsonTransport
from ai_data_enrichment.providers.open_meteo import OpenMeteoGeocodingHandler
from ai_data_enrichment.providers.open_meteo_resolution import OpenMeteoResolutionService
from ai_data_enrichment.providers.registry import ProviderRegistry
from ai_data_enrichment.providers.request_policy import (
    ProviderRequestPolicy,
    ProviderRequestSession,
)
from ai_data_enrichment.providers.retry_transport import TransportRetryPolicy
from ai_data_enrichment.providers.wikidata import (
    WikidataElevationHandler,
    WikidataMountainResolver,
)
from ai_data_enrichment.providers.wikidata_approved import ApprovedWikidataMountainResolutionService
from ai_data_enrichment.providers.wikidata_resolution import (
    WikidataMountainResolutionService,
)
from ai_data_enrichment.resolution.models import ResolutionPolicy
from ai_data_enrichment.resolution.resume import ResumeSelection


def build_live_executor(
    registry: CapabilityRegistry,
    *,
    max_rows: int = 20,
    audit_mountains: bool = False,
    resume_selections: Mapping[tuple[str, str], ResumeSelection] | None = None,
    audit_capitals: bool = False,
    mountain_resolution_policy: Literal["strict", "verified_only"] = "strict",
    request_policies: Mapping[str, ProviderRequestPolicy] | None = None,
    transport: JsonTransport | None = None,
) -> JobExecutor:
    """Bind built-in capabilities to explicitly approved providers."""

    if resume_selections and not audit_mountains:
        raise ValueError("Resume requires audited mountain resolution.")
    if mountain_resolution_policy not in {"strict", "verified_only"}:
        raise ValueError("Unsupported mountain resolution policy.")
    if mountain_resolution_policy != "strict" and not audit_mountains:
        raise ValueError("Verified-only policy requires audited mountain resolution.")

    transport = transport or UrllibJsonTransport(
        headers={
            "User-Agent": (
                "AIDataEnrichmentStudio/0.1 (https://github.com/ayri77/ai-data-enrichment-studio)"
            )
        }
    )
    session = ProviderRequestSession(request_policies) if request_policies else None
    city_transport = (
        session.wrap_transport("open-meteo", transport, retry_policy=TransportRetryPolicy())
        if session
        else transport
    )
    mountain_transport = (
        session.wrap_transport("wikidata", transport, retry_policy=TransportRetryPolicy())
        if session
        else transport
    )
    providers = ProviderRegistry(registry)

    providers.register(
        capability_id="geocode",
        provider_id="open-meteo",
        handler=OpenMeteoGeocodingHandler(city_transport),
    )
    providers.register(
        capability_id="geodesic_distance",
        provider_id="local",
        handler=GeodesicDistanceHandler(),
    )
    providers.register(
        capability_id="resolve_entity",
        provider_id="wikidata",
        handler=WikidataMountainResolver(mountain_transport),
    )
    providers.register(
        capability_id="lookup_property",
        provider_id="wikidata",
        handler=WikidataElevationHandler(mountain_transport),
    )

    selected = {
        "geocode": "open-meteo",
        "geodesic_distance": "local",
        "resolve_entity": "wikidata",
        "lookup_property": "wikidata",
    }

    return JobExecutor(
        request_session=session,
        handlers=providers.resolve_handlers(
            capability_ids=selected,
            selected_providers=selected,
        ),
        selected_providers=selected,
        configuration_ids={
            "geocode": "open-meteo-v1",
            "geodesic_distance": "haversine-v1",
            "resolve_entity": "wikidata-v1",
            "lookup_property": "wikidata-v1",
        },
        policy=JobExecutionPolicy(
            max_rows=max_rows,
            batch_size=min(max_rows, 20),
            max_memoized_results=max_rows,
            memoizable_providers=frozenset(
                (capability_id, provider_id) for capability_id, provider_id in selected.items()
            ),
        ),
        capability_versions={
            **({"resolve_entity": "1.0"} if audit_mountains else {}),
            **({"geocode": "1.1"} if audit_capitals else {}),
        },
        provider_versions={
            **({"resolve_entity": "wikidata-v1"} if audit_mountains else {}),
            **({"geocode": "open-meteo-v1"} if audit_capitals else {}),
        },
        resolution_services={
            **(
                {"resolve_entity": WikidataMountainResolutionService(mountain_transport)}
                if audit_mountains
                else {}
            ),
            **({"geocode": OpenMeteoResolutionService(city_transport)} if audit_capitals else {}),
        },
        resolution_policies={
            **(
                {
                    "resolve_entity": ResolutionPolicy(
                        policy_id=f"mountain_{mountain_resolution_policy}",
                        version="1.0",
                        strategy=mountain_resolution_policy,
                    )
                }
                if audit_mountains
                else {}
            ),
            **(
                {
                    "geocode": ResolutionPolicy(
                        policy_id="capital_strict",
                        version="1.0",
                        strategy="strict",
                    )
                }
                if audit_capitals
                else {}
            ),
        },
        approved_resolution_services={
            key: ApprovedWikidataMountainResolutionService(
                mountain_transport,
                approval=selection.approval,
                source_report=selection.source_report,
            )
            for key, selection in (resume_selections or {}).items()
        },
    )
