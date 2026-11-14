"""What each built-in capability is, beyond its contract.

A ``CapabilitySpec`` says what the planner may ask for. A binding says what the
rest of the code has to know about the same capability: where its values come
from, how far they can be trusted, whether a confirmed value may be reused, and,
for a capability that resolves a name, what review and "search under another
name" need. This table is the one place that states it; the executor assembly,
the run worker, the resolution job and the review services read it instead of
comparing capability ids.

The handlers themselves are not here: they need transports and stay assembled
in ``orchestration/live_executor.py``, keyed by the ids below.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from ai_data_enrichment.providers.openrouteservice import ROUTING_PROVIDER


class Trust(StrEnum):
    """What stands behind a value, in the terms the audit uses."""

    # Deterministic arithmetic on typed inputs; nothing to verify.
    COMPUTED = "computed"
    # Read from a record of an identified entity.
    RECORD = "record"
    # A name resolved to an entity: candidates, a verification rule, a policy.
    RESOLVED_NAME = "resolved_name"
    # Computed by an outside service; there is no record to open and check.
    SERVICE = "service"


def _parameters(*items: dict[str, str]) -> tuple[Mapping[str, str], ...]:
    return tuple(MappingProxyType(item) for item in items)


@dataclass(frozen=True)
class NameResolution:
    """What a capability that resolves a name states about its review."""

    # The input that holds the name, and the kind of entity it names.
    name_input: str
    entity_type: str
    # Step parameters under which the lookup is audited, approved and may be
    # searched under another name; any other parameters are refused.
    approved_parameters: tuple[Mapping[str, str], ...]
    # Inputs besides the name and the country that narrow a search, and the
    # step parameters that are part of the audited query's context.
    context_inputs: tuple[str, ...]
    context_parameters: tuple[str, ...]
    # Whether the resolution service takes the step parameters as an argument.
    service_takes_parameters: bool
    # What the source matches a name against; told to a model that is asked for
    # other names.
    match_description: str


@dataclass(frozen=True)
class CapabilityBinding:
    capability_id: str
    # The source that answers for it in a live run, or "local" for a
    # calculation done in this process.
    source_id: str
    trust: Trust
    # Whether a value confirmed by one run may be taken over by a later run.
    reusable: bool
    # Bound whether or not a registry lists it: the four of release 0.1.0. The
    # others are bound only where the registry holds them.
    always_bound: bool
    # Offered only where a personal key for the source is configured.
    requires_key: bool = False
    name_resolution: NameResolution | None = None

    @property
    def optional(self) -> bool:
        return self.requires_key


# Order is the order the executor has always listed them in.
BINDINGS: tuple[CapabilityBinding, ...] = (
    CapabilityBinding(
        capability_id="geocode",
        source_id="open-meteo",
        trust=Trust.RESOLVED_NAME,
        reusable=True,
        always_bound=True,
        name_resolution=NameResolution(
            name_input="city",
            entity_type="city",
            approved_parameters=_parameters({}, {"required_role": "country_capital"}),
            context_inputs=("administrative_area",),
            context_parameters=("required_role",),
            service_takes_parameters=True,
            match_description="GeoNames; a name must equal the entry's English name",
        ),
    ),
    CapabilityBinding(
        capability_id="geodesic_distance",
        source_id="local",
        trust=Trust.COMPUTED,
        reusable=False,
        always_bound=True,
    ),
    CapabilityBinding(
        capability_id="resolve_entity",
        source_id="wikidata",
        trust=Trust.RESOLVED_NAME,
        reusable=True,
        always_bound=True,
        name_resolution=NameResolution(
            name_input="name",
            entity_type="mountain",
            approved_parameters=_parameters({"entity_type": "mountain"}),
            context_inputs=(),
            context_parameters=(),
            service_takes_parameters=False,
            match_description="Wikidata; a name must equal an English label or alias",
        ),
    ),
    CapabilityBinding(
        capability_id="lookup_property",
        source_id="wikidata",
        trust=Trust.RECORD,
        reusable=True,
        always_bound=True,
    ),
    CapabilityBinding(
        capability_id="ratio",
        source_id="local",
        trust=Trust.COMPUTED,
        reusable=False,
        always_bound=False,
    ),
    CapabilityBinding(
        capability_id="road_distance",
        source_id=ROUTING_PROVIDER,
        trust=Trust.SERVICE,
        reusable=True,
        always_bound=False,
        requires_key=True,
    ),
)

_BY_ID: Mapping[str, CapabilityBinding] = MappingProxyType(
    {binding.capability_id: binding for binding in BINDINGS}
)


def binding_for(capability_id: str) -> CapabilityBinding:
    """The binding of a capability; an id without one is not a built-in."""
    return _BY_ID[capability_id]


def find_binding(capability_id: str) -> CapabilityBinding | None:
    return _BY_ID.get(capability_id)


def name_resolvers() -> tuple[CapabilityBinding, ...]:
    """The capabilities that resolve a name, in table order."""
    return tuple(item for item in BINDINGS if item.name_resolution is not None)


def name_resolution_for(capability_id: str) -> NameResolution:
    """What a capability that resolves a name states; an error for any other."""
    resolution = binding_for(capability_id).name_resolution
    if resolution is None:
        raise ValueError(f"Capability {capability_id!r} does not resolve a name.")
    return resolution
