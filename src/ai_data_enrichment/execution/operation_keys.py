"""Canonical typed operation identity shared by memory and persistent caches."""

from collections.abc import Mapping
from hashlib import sha256
from json import dumps

from ai_data_enrichment.execution.models import (
    EntityRef,
    GeoPoint,
    Quantity,
    RuntimeValue,
)

# Change this when the canonical representation or its meaning changes.
OPERATION_KEY_VERSION = "operation-key:v1"

_ValueKey = tuple[str, ...]
_OperationKey = tuple[
    str,
    str,
    str,
    tuple[tuple[str, _ValueKey], ...],
    tuple[tuple[str, str], ...],
]


def _typed_value_key(value: RuntimeValue) -> _ValueKey | None:
    """Encode a known runtime value without conflating types or normalizing names."""
    if isinstance(value, str):
        return ("text", value)
    if type(value) is float:
        # Nonfinite runtime values are never eligible for memoization.
        from math import isfinite

        if not isfinite(value):
            return None
        return ("float", value.hex())
    if isinstance(value, GeoPoint):
        return ("geo_point", value.latitude.hex(), value.longitude.hex())
    if isinstance(value, EntityRef):
        return ("entity_ref", value.source, value.entity_id)
    if isinstance(value, Quantity):
        return ("quantity", value.value.hex(), value.unit)
    return None


def _operation_key(
    *,
    capability_id: str,
    provider_id: str,
    configuration_id: str,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, str],
) -> _OperationKey | None:
    """Build a deterministic key, or bypass memoization for unsafe inputs."""
    input_parts: list[tuple[str, _ValueKey]] = []
    for name, value in inputs.items():
        if not isinstance(name, str):
            return None
        encoded = _typed_value_key(value)
        if encoded is None:
            return None
        input_parts.append((name, encoded))

    parameter_parts: list[tuple[str, str]] = []
    for name, value in parameters.items():
        if not isinstance(name, str) or not isinstance(value, str):
            return None
        parameter_parts.append((name, value))

    return (
        capability_id,
        provider_id,
        configuration_id,
        tuple(sorted(input_parts)),
        tuple(sorted(parameter_parts)),
    )


def persistent_key_digest(
    *,
    capability_id: str,
    capability_version: str,
    provider_id: str,
    provider_version: str,
    configuration_id: str,
    inputs: Mapping[str, RuntimeValue],
    parameters: Mapping[str, str],
) -> str | None:
    """Return a deterministic, versioned SHA-256 lookup key or no eligible key.

    Caller-controlled IDs must describe semantic configuration and must never
    contain secrets. Operation inputs should never be printed or logged.
    """
    identifiers = (
        capability_id,
        capability_version,
        provider_id,
        provider_version,
        configuration_id,
    )
    if any(not identifier.strip() for identifier in identifiers):
        raise ValueError("Capability/provider versions and identities must not be blank.")

    identity = _operation_key(
        capability_id=capability_id,
        provider_id=provider_id,
        configuration_id=configuration_id,
        inputs=inputs,
        parameters=parameters,
    )
    if identity is None:
        return None

    canonical = dumps(
        [OPERATION_KEY_VERSION, capability_version, provider_version, identity],
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
