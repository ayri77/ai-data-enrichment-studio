"""Configure approved live providers for Excel enrichment."""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, cast

from ai_data_enrichment.capabilities.bindings import (
    BINDINGS,
    FALLBACK_BINDINGS,
    name_resolvers,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.execution.contracts import CapabilityHandler
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
from ai_data_enrichment.providers.web_lookup import (
    LOOKUP_CAPABILITY,
    WebLookupClient,
    WebLookupUsage,
)
from ai_data_enrichment.providers.web_lookup_handler import (
    DEFAULT_TIMEOUT_SECONDS,
    WebLookupHandler,
)
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
from ai_data_enrichment.run_limits import RESULT_REUSE_SECONDS

# Where the values of each capability come from in a live run: the id of the
# source that answers for it, or "local" for a calculation done in this
# process. Derived from the bindings, so the executor and a client are told the
# same.
LIVE_SOURCES: Mapping[str, str] = MappingProxyType(
    {binding.capability_id: binding.source_id for binding in BINDINGS}
)


@dataclass(frozen=True)
class WebLookupOptions:
    """What the ``web_lookup`` fallback needs to run: the API object and how to use it.

    Passed to ``build_live_executor`` only by a caller that runs a plan the server
    built for the fallback. ``api`` is any object with ``responses.create(**kwargs)``;
    ``usage`` receives one count per request actually made.
    """

    api: WebLookupClient
    model: str
    reasoning_effort: str | None
    usage: WebLookupUsage
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    # A point on ``clock`` after which no further request is started.
    deadline: float | None = None
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("The web lookup model must not be blank.")
        if not self.timeout_seconds > 0:
            raise ValueError("The web lookup timeout must be positive.")


# The source and the job-wide operation budget of the fallback: not in BINDINGS.
_LOOKUP_BINDING = next(
    item for item in FALLBACK_BINDINGS if item.capability_id == LOOKUP_CAPABILITY
)


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
    reuse_seconds: float = RESULT_REUSE_SECONDS,
    routing_key: str | None = None,
    web_lookup: WebLookupOptions | None = None,
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

    A registry that holds ``web_lookup`` (the one built for a fallback plan) needs
    ``web_lookup``: without it the assembly refuses, so that no row fails one by one.
    The handler is bound under the source of ``FALLBACK_BINDINGS``, gets an operation
    budget of ``max_rows`` for the job and is memoized in memory within the job. With
    ``reuse`` its successful answers are also kept for ``reuse_seconds`` in the plan's
    value cache, like a value read from a source, and a later job takes them over before
    the budget or the API is touched; failures are never kept. Without such a registry
    ``web_lookup`` is ignored.
    """

    selections = dict(resume_selections or {})
    approved_sources = {
        item.approval.source for item in selections.values() if isinstance(item, ResumeSelection)
    }
    renamed = {item.capability for item in selections.values() if isinstance(item, OtherName)}
    resolvers = name_resolvers()
    if approved_sources - {item.source_id for item in resolvers}:
        raise ValueError("Resume selection uses an unsupported source.")
    audited = {"resolve_entity": audit_mountains, "geocode": audit_capitals}
    # Mountains are checked before cities, as the messages have always been.
    for item in sorted(resolvers, key=lambda binding: binding.capability_id != "resolve_entity"):
        resolution = item.name_resolution
        if resolution is None:
            continue
        if (item.source_id in approved_sources or item.capability_id in renamed) and not audited[
            item.capability_id
        ]:
            raise ValueError(f"Resume requires audited {resolution.entity_type} resolution.")
    if mountain_resolution_policy not in {"strict", "verified_only"}:
        raise ValueError("Unsupported mountain resolution policy.")
    if mountain_resolution_policy != "strict" and not audit_mountains:
        raise ValueError("Verified-only policy requires audited mountain resolution.")

    with_lookup = registry.get(LOOKUP_CAPABILITY) is not None
    if with_lookup and web_lookup is None:
        raise ValueError("A plan with web_lookup needs the web lookup options to be executed.")
    lookup_source = _LOOKUP_BINDING.source_id
    if with_lookup and request_policies and lookup_source in request_policies:
        raise ValueError("The request limit of the web lookup is set by the assembly.")

    routing_configuration = "openrouteservice-driving-car-v1"
    with_ratio = registry.get("ratio") is not None
    with_roads = registry.get("road_distance") is not None
    if with_roads and transport is None and not routing_key:
        raise ValueError("Road distance requires a routing key.")

    agent = {
        "User-Agent": (
            "AIDataEnrichmentStudio/0.3 (https://github.com/ayri77/ai-data-enrichment-studio)"
        )
    }
    # One session for the job. The lookup's budget is added to the caller's policies; the
    # transports of the other sources are wrapped only where the caller gave policies,
    # so a session made for the lookup alone changes nothing for them.
    policies = dict(request_policies or {})
    if with_lookup:
        # One lookup request per row at most: the budget counts handler calls the
        # job-scoped memo did not answer, so a job cannot start more than max_rows.
        # The lookup makes no transport attempts; the request limit only mirrors it.
        policies[lookup_source] = ProviderRequestPolicy(
            max_requests_per_job=max_rows, max_operations_per_job=max_rows
        )
    session = ProviderRequestSession(policies) if policies else None
    transport_session = session if request_policies else None
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
            transport_session.wrap_transport(
                ROUTING_PROVIDER, routing, retry_policy=TransportRetryPolicy()
            )
            if transport_session and transport_session.has_provider_policy(ROUTING_PROVIDER)
            else routing,
        )
    transport = transport or UrllibJsonTransport(headers=agent)
    city_transport = (
        transport_session.wrap_transport(
            "open-meteo", transport, retry_policy=TransportRetryPolicy()
        )
        if transport_session
        else transport
    )
    mountain_transport = (
        transport_session.wrap_transport("wikidata", transport, retry_policy=TransportRetryPolicy())
        if transport_session
        else transport
    )
    providers = ProviderRegistry(registry)
    mountain_service = WikidataMountainResolutionService(mountain_transport)
    city_service = OpenMeteoResolutionService(city_transport)
    # One service per reviewed operation: rows that share a name share a search.
    services = {"open-meteo": city_service, "wikidata": mountain_service}
    other_names = {
        (item.operation_id, item.searched_as): OtherNameResolutionService(
            services[LIVE_SOURCES[item.capability]],
            capability=item.capability,
            row_name=item.row_name,
            searched_as=item.searched_as,
        )
        for item in selections.values()
        if isinstance(item, OtherName)
    }

    handlers: dict[str, CapabilityHandler] = {
        "geocode": OpenMeteoGeocodingHandler(city_transport),
        "geodesic_distance": GeodesicDistanceHandler(),
        "resolve_entity": WikidataMountainResolver(mountain_transport),
        "lookup_property": WikidataElevationHandler(mountain_transport),
    }
    if with_ratio:
        handlers["ratio"] = RatioHandler()
    if routing_transport is not None:
        handlers["road_distance"] = OpenRouteServiceDistanceHandler(routing_transport)
    lookup_handler: WebLookupHandler | None = None
    if with_lookup:
        assert web_lookup is not None
        lookup_handler = WebLookupHandler(
            web_lookup.api,
            model=web_lookup.model,
            reasoning_effort=web_lookup.reasoning_effort,
            usage=web_lookup.usage,
            timeout_seconds=web_lookup.timeout_seconds,
            clock=web_lookup.clock,
            deadline=web_lookup.deadline,
        )
    for capability, handler in handlers.items():
        providers.register(
            capability_id=capability,
            provider_id=LIVE_SOURCES[capability],
            handler=handler,
        )
    selected = {
        binding.capability_id: binding.source_id
        for binding in BINDINGS
        if binding.always_bound or registry.get(binding.capability_id) is not None
    }
    if lookup_handler is not None:
        # Bound under the fallback's own source, outside BINDINGS and LIVE_SOURCES. Being in
        # `selected` makes its answers memoizable within the job; the persistent TTL of its
        # binding is added below, from the same lifetime as every other capability's.
        providers.register(
            capability_id=LOOKUP_CAPABILITY, provider_id=lookup_source, handler=lookup_handler
        )
        selected[LOOKUP_CAPABILITY] = lookup_source

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
            **(
                {LOOKUP_CAPABILITY: lookup_handler.configuration_id}
                if lookup_handler is not None
                else {}
            ),
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
                    (binding.capability_id, binding.source_id): reuse_seconds
                    for binding in BINDINGS
                    if binding.reusable
                    and binding.capability_id in selected
                    # A name is reused only where its resolution is audited.
                    and (binding.name_resolution is None or audited[binding.capability_id])
                }
                | (
                    {(LOOKUP_CAPABILITY, lookup_source): reuse_seconds}
                    if lookup_handler is not None and _LOOKUP_BINDING.reusable
                    else {}
                )
                if reuse
                else {}
            ),
            # The search's answer "nothing found" is kept like a found answer.
            kept_nothing_found=(
                frozenset({(LOOKUP_CAPABILITY, lookup_source)})
                if reuse and lookup_handler is not None and _LOOKUP_BINDING.reusable
                else frozenset()
            ),
        ),
        persistent_cache=reuse[0] if reuse else None,
        resolution_cache=reuse[1] if reuse else None,
        capability_versions={
            **({"resolve_entity": "1.0"} if audit_mountains else {}),
            **({"geocode": "1.2"} if audit_capitals else {}),
            **({"lookup_property": "1.0"} if reuse else {}),
            **({"road_distance": "1.0"} if reuse and with_roads else {}),
            **({LOOKUP_CAPABILITY: "1.0"} if reuse and lookup_handler is not None else {}),
        },
        provider_versions={
            **({"resolve_entity": "wikidata-v1"} if audit_mountains else {}),
            **({"geocode": "open-meteo-v1"} if audit_capitals else {}),
            **({"lookup_property": "wikidata-v1"} if reuse else {}),
            **({"road_distance": routing_configuration} if reuse and with_roads else {}),
            **(
                {LOOKUP_CAPABILITY: lookup_handler.configuration_id}
                if reuse and lookup_handler is not None
                else {}
            ),
        },
        resolution_services={
            item.capability_id: services[item.source_id]
            for item in resolvers
            if audited[item.capability_id]
        },
        partial_input_capabilities=frozenset({LOOKUP_CAPABILITY} if with_lookup else ()),
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
