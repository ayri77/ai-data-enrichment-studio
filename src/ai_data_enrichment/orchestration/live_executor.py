"""Configure approved live providers for Excel enrichment."""

from collections.abc import Mapping
from typing import Literal, cast

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import JobExecutionPolicy
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.http_transport import (
    JsonPostTransport,
    JsonTransport,
    UrllibJsonTransport,
)
from ai_data_enrichment.providers.open_meteo import OpenMeteoGeocodingHandler
from ai_data_enrichment.providers.open_meteo_approved import ApprovedOpenMeteoResolutionService
from ai_data_enrichment.providers.open_meteo_resolution import OpenMeteoResolutionService
from ai_data_enrichment.providers.openrouteservice import (
    ROUTING_PROVIDER,
    OpenRouteServiceDistanceHandler,
)
from ai_data_enrichment.providers.ratio import RatioHandler
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
from ai_data_enrichment.resolution.other_name import OtherNameResolutionService
from ai_data_enrichment.resolution.resume import OtherName, ResumeSelection
from ai_data_enrichment.resolution.sqlite_cache import SQLiteResolutionCache


def build_live_executor(
    registry: CapabilityRegistry,
    *,
    max_rows: int = 20,
    audit_mountains: bool = False,
    resume_selections: Mapping[tuple[str, str], ResumeSelection | OtherName] | None = None,
    audit_capitals: bool = False,
    mountain_resolution_policy: Literal["strict", "verified_only"] = "strict",
    request_policies: Mapping[str, ProviderRequestPolicy] | None = None,
    transport: JsonTransport | None = None,
    reuse: tuple[SQLiteOperationCache, SQLiteResolutionCache] | None = None,
    reuse_seconds: float = 3600,
    routing_key: str | None = None,
) -> JobExecutor:
    """Bind the registered capabilities to explicitly approved providers.

    The four built-in capabilities are always bound. ``ratio`` and
    ``road_distance`` are bound when the registry holds them; a road distance
    additionally needs ``routing_key`` or an injected transport, because its
    source answers only with a personal key.

    ``reuse`` lets a run take what an earlier run with the same caches already
    confirmed instead of asking the source again: verified entities with their
    reports, and values read from a source record. Only successes are kept, so
    a name without a confirmed match is asked for again, and a user's own
    selection never comes from a cache.
    """

    selections = dict(resume_selections or {})
    approved_sources = {
        item.approval.source for item in selections.values() if isinstance(item, ResumeSelection)
    }
    renamed = {item.capability for item in selections.values() if isinstance(item, OtherName)}
    if approved_sources - {"wikidata", "open-meteo"}:
        raise ValueError("Resume selection uses an unsupported source.")
    if ("wikidata" in approved_sources or "resolve_entity" in renamed) and not audit_mountains:
        raise ValueError("Resume requires audited mountain resolution.")
    if ("open-meteo" in approved_sources or "geocode" in renamed) and not audit_capitals:
        raise ValueError("Resume requires audited city resolution.")
    if mountain_resolution_policy not in {"strict", "verified_only"}:
        raise ValueError("Unsupported mountain resolution policy.")
    if mountain_resolution_policy != "strict" and not audit_mountains:
        raise ValueError("Verified-only policy requires audited mountain resolution.")

    routing_configuration = "openrouteservice-driving-car-v1"
    with_ratio = registry.get("ratio") is not None
    with_roads = registry.get("road_distance") is not None
    if with_roads and transport is None and not routing_key:
        raise ValueError("Road distance requires a routing key.")

    agent = {
        "User-Agent": (
            "AIDataEnrichmentStudio/0.1 (https://github.com/ayri77/ai-data-enrichment-studio)"
        )
    }
    session = ProviderRequestSession(request_policies) if request_policies else None
    routing_transport: JsonPostTransport | None = None
    if with_roads:
        # The key travels in a header of the routing transport only; the other
        # sources never receive it. An injected transport answers for all of them.
        routing = transport or UrllibJsonTransport(
            headers={**agent, "Authorization": routing_key or ""}
        )
        if not callable(getattr(routing, "post_json", None)):
            raise ValueError("Road distance requires a transport that can send a JSON body.")
        routing_transport = cast(
            JsonPostTransport,
            session.wrap_transport(ROUTING_PROVIDER, routing, retry_policy=TransportRetryPolicy())
            if session and session.has_provider_policy(ROUTING_PROVIDER)
            else routing,
        )
    transport = transport or UrllibJsonTransport(headers=agent)
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
    mountain_service = WikidataMountainResolutionService(mountain_transport)
    city_service = OpenMeteoResolutionService(city_transport)
    # One service per reviewed operation: rows that share a name share a search.
    other_names = {
        (item.operation_id, item.searched_as): OtherNameResolutionService(
            city_service if item.capability == "geocode" else mountain_service,
            capability=item.capability,
            row_name=item.row_name,
            searched_as=item.searched_as,
        )
        for item in selections.values()
        if isinstance(item, OtherName)
    }

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

    if with_ratio:
        providers.register(capability_id="ratio", provider_id="local", handler=RatioHandler())
    if routing_transport is not None:
        providers.register(
            capability_id="road_distance",
            provider_id=ROUTING_PROVIDER,
            handler=OpenRouteServiceDistanceHandler(routing_transport),
        )

    selected = {
        "geocode": "open-meteo",
        "geodesic_distance": "local",
        "resolve_entity": "wikidata",
        "lookup_property": "wikidata",
        **({"ratio": "local"} if with_ratio else {}),
        **({"road_distance": ROUTING_PROVIDER} if with_roads else {}),
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
            **({"ratio": "ratio-v1"} if with_ratio else {}),
            **({"road_distance": routing_configuration} if with_roads else {}),
        },
        policy=JobExecutionPolicy(
            max_rows=max_rows,
            batch_size=min(max_rows, 20),
            max_memoized_results=max_rows,
            memoizable_providers=frozenset(
                (capability_id, provider_id) for capability_id, provider_id in selected.items()
            ),
            # What a source was asked for only; a straight-line distance and a
            # ratio are computed here and cost nothing to compute again.
            persistent_cache_ttls=(
                {
                    **({("resolve_entity", "wikidata"): reuse_seconds} if audit_mountains else {}),
                    **({("geocode", "open-meteo"): reuse_seconds} if audit_capitals else {}),
                    ("lookup_property", "wikidata"): reuse_seconds,
                    **({("road_distance", ROUTING_PROVIDER): reuse_seconds} if with_roads else {}),
                }
                if reuse
                else {}
            ),
        ),
        persistent_cache=reuse[0] if reuse else None,
        resolution_cache=reuse[1] if reuse else None,
        capability_versions={
            **({"resolve_entity": "1.0"} if audit_mountains else {}),
            **({"geocode": "1.1"} if audit_capitals else {}),
            **({"lookup_property": "1.0"} if reuse else {}),
            **({"road_distance": "1.0"} if reuse and with_roads else {}),
        },
        provider_versions={
            **({"resolve_entity": "wikidata-v1"} if audit_mountains else {}),
            **({"geocode": "open-meteo-v1"} if audit_capitals else {}),
            **({"lookup_property": "wikidata-v1"} if reuse else {}),
            **({"road_distance": routing_configuration} if reuse and with_roads else {}),
        },
        resolution_services={
            **({"resolve_entity": mountain_service} if audit_mountains else {}),
            **({"geocode": city_service} if audit_capitals else {}),
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
        # Each approval is re-verified against the source that produced it.
        approved_resolution_services={
            key: (
                other_names[(selection.operation_id, selection.searched_as)]
                if isinstance(selection, OtherName)
                else ApprovedWikidataMountainResolutionService(
                    mountain_transport,
                    approval=selection.approval,
                    source_report=selection.source_report,
                )
                if selection.approval.source == "wikidata"
                else ApprovedOpenMeteoResolutionService(
                    city_transport,
                    approval=selection.approval,
                    source_report=selection.source_report,
                )
            )
            for key, selection in selections.items()
        },
    )
