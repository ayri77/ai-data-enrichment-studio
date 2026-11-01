"""Offline tests for shared canonical operation identity and typed result codec."""

from hashlib import sha256
from json import dumps, loads

import pytest

from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.execution.operation_keys import (
    OPERATION_KEY_VERSION,
    _operation_key,
    persistent_key_digest,
)
from ai_data_enrichment.execution.result_codec import (
    RESULT_FORMAT_VERSION,
    deserialize_success,
    serialize_success,
)


def digest(
    *,
    inputs: dict[str, RuntimeValue] | None = None,
    parameters: dict[str, str] | None = None,
    capability: str = "resolve_entity",
    capability_version: str = "1.0",
    provider: str = "wikidata",
    provider_version: str = "mountain-v1",
    configuration: str = "en-P31-P279",
) -> str | None:
    return persistent_key_digest(
        capability_id=capability,
        capability_version=capability_version,
        provider_id=provider,
        provider_version=provider_version,
        configuration_id=configuration,
        inputs=inputs if inputs is not None else {"name": "Everest"},
        parameters=parameters if parameters is not None else {"entity_type": "mountain"},
    )


def test_digest_is_repeatable_versioned_and_structurally_order_independent() -> None:
    first = digest(
        inputs={"name": "Everest", "country": "Nepal"},
        parameters={"entity_type": "mountain", "language": "en"},
    )
    second = digest(
        inputs={"country": "Nepal", "name": "Everest"},
        parameters={"language": "en", "entity_type": "mountain"},
    )
    assert first == second
    assert isinstance(first, str)
    assert len(first) == 64
    assert first == first.lower()
    assert OPERATION_KEY_VERSION == "operation-key:v1"

    changed = digest(inputs={"country": "Nepal", "name": " Everest "})
    assert changed != first
    assert digest() == digest()


@pytest.mark.parametrize(
    "changed",
    [
        {"capability": "lookup_property"},
        {"capability_version": "2.0"},
        {"provider": "other"},
        {"provider_version": "mountain-v2"},
        {"configuration": "de-P31-P279"},
        {"parameters": {"entity_type": "hill"}},
        {"inputs": {"name": "everest"}},
    ],
)
def test_identity_changes_cannot_return_same_digest(changed: dict[str, object]) -> None:
    initial = digest()
    variation = digest(
        capability=str(changed.get("capability", "resolve_entity")),
        capability_version=str(changed.get("capability_version", "1.0")),
        provider=str(changed.get("provider", "wikidata")),
        provider_version=str(changed.get("provider_version", "mountain-v1")),
        configuration=str(changed.get("configuration", "en-P31-P279")),
        inputs={"name": "everest"} if "inputs" in changed else None,
        parameters={"entity_type": "hill"} if "parameters" in changed else None,
    )
    assert variation != initial


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({"value": "1.0"}, {"value": 1.0}),
        ({"value": 1.0}, {"value": -0.0}),
        (
            {"value": EntityRef(source="wikidata", entity_id="Q513")},
            {"value": EntityRef(source="another", entity_id="Q513")},
        ),
        (
            {"value": Quantity(value=12.0, unit="m")},
            {"value": Quantity(value=12.0, unit="km")},
        ),
        (
            {"value": GeoPoint(latitude=1.0, longitude=2.0)},
            {"value": GeoPoint(latitude=2.0, longitude=1.0)},
        ),
    ],
)
def test_typed_identity_matches_in_memory_semantics(
    left: dict[str, RuntimeValue], right: dict[str, RuntimeValue]
) -> None:
    assert digest(inputs=left) != digest(inputs=right)
    memory_left = _operation_key(
        capability_id="resolve_entity",
        provider_id="wikidata",
        configuration_id="en-P31-P279",
        inputs=left,
        parameters={"entity_type": "mountain"},
    )
    memory_right = _operation_key(
        capability_id="resolve_entity",
        provider_id="wikidata",
        configuration_id="en-P31-P279",
        inputs=right,
        parameters={"entity_type": "mountain"},
    )
    assert memory_left != memory_right


def test_unsupported_or_nonfinite_inputs_do_not_generate_cache_keys() -> None:
    assert digest(inputs={"bad": float("nan")}) is None
    assert digest(inputs={"bad": float("inf")}) is None


@pytest.mark.parametrize(
    "field",
    ["capability", "capability_version", "provider", "provider_version", "configuration"],
)
def test_blank_identity_fields_rejected(field: str) -> None:
    values = {
        "capability": "resolve_entity",
        "capability_version": "1.0",
        "provider": "wikidata",
        "provider_version": "mountain-v1",
        "configuration": "en-P31-P279",
    }
    values[field] = " "
    with pytest.raises(ValueError, match="not be blank"):
        digest(
            capability=values["capability"],
            capability_version=values["capability_version"],
            provider=values["provider"],
            provider_version=values["provider_version"],
            configuration=values["configuration"],
        )


@pytest.mark.parametrize(
    "value",
    [
        "Everest",
        8848.86,
        -0.0,
        1.0e-300,
        GeoPoint(latitude=27.9881, longitude=86.925),
        EntityRef(source="wikidata", entity_id="Q513"),
        Quantity(value=8848.86, unit="m"),
    ],
)
def test_successful_typed_result_and_ordered_evidence_round_trip(value: RuntimeValue) -> None:
    result = OperationResult(
        status="success",
        value=value,
        evidence=(
            Evidence(source="wikidata", reference="Q513:P31"),
            Evidence(source="wikidata", reference="Q513:P2044"),
        ),
    )
    encoded = serialize_success(result)
    restored = deserialize_success(encoded)
    assert restored == result
    assert type(restored.value) is type(result.value)
    assert restored.evidence == result.evidence
    assert serialize_success(restored) == encoded
    assert RESULT_FORMAT_VERSION == "operation-result:v1"


def test_unicode_strings_and_empty_evidence_round_trip() -> None:
    result = OperationResult(status="success", value="Говерла 山 🏔")
    encoded = serialize_success(result)
    assert "Говерла" in encoded
    assert deserialize_success(encoded) == result


def test_codec_rejects_failed_operation() -> None:
    failed = OperationResult(status="failure", error_code="NOT_FOUND", message="No value.")
    with pytest.raises(ValueError, match="Only successful"):
        serialize_success(failed)


@pytest.mark.parametrize(
    "mutation",
    [
        {"format": "operation-result:v2"},
        {"status": "failure"},
        {"value": {"type": "unknown", "value": "hi"}},
        {"value": {"type": "text"}},
        {"value": {"type": "float", "value": "nan"}},
        {"value": {"type": "float", "value": "0.1"}},
        {"value": {"type": "quantity", "value": "0x1.0p+1", "unit": ""}},
        {"value": {"type": "geo_point", "latitude": "0x1.0p+7", "longitude": "0x1.0p+0"}},
        {"evidence": {"source": "wikidata"}},
        {"evidence": [{"source": "wikidata", "reference": ""}]},
    ],
)
def test_invalid_payload_is_rejected(mutation: dict[str, object]) -> None:
    encoded = serialize_success(OperationResult(status="success", value="Everest", evidence=()))
    document = loads(encoded)
    document.update(mutation)
    with pytest.raises(ValueError):
        deserialize_success(dumps(document))


@pytest.mark.parametrize(
    "payload",
    [
        "not JSON",
        "{}",
        "[]",
        "null",
        '{"format":"operation-result:v1"}',
        '{"format":"operation-result:v1","status":"success","value":{"type":"text","value":"A"},"evidence":[],"extra":"x"}',
        '{"format":"operation-result:v1","status":"success","value":{"type":"text","value":"A","untrusted":true},"evidence":[]}',
    ],
)
def test_malformed_or_extra_payload_fields_are_rejected(payload: str) -> None:
    with pytest.raises(ValueError):
        deserialize_success(payload)


def test_digest_uses_canonical_hash_not_plaintext_key() -> None:
    key = digest(inputs={"name": "private sample"})
    assert key is not None
    assert "private sample" not in key
    assert key != sha256(b"private sample").hexdigest()
