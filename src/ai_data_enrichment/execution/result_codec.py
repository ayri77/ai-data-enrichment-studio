"""Strict, versioned JSON codec for verified successful operation results."""

from json import dumps, loads
from math import isfinite

from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)

RESULT_FORMAT_VERSION = "operation-result:v1"


def _required_object(value: object, fields: frozenset[str]) -> dict[str, object]:
    """Reject absent, unexpected, or non-string object keys."""
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Invalid cache payload object fields.")
    if any(not isinstance(key, str) for key in value):
        raise ValueError("Cache payload object keys must be strings.")
    return value


def _required_string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Cache payload string field has the wrong type.")
    return value


def _hex_float(value: float) -> str:
    if not isfinite(value):
        raise ValueError("Non-finite numeric cache values are not supported.")
    return value.hex()


def _parse_hex_float(value: object) -> float:
    text = _required_string(value)
    try:
        number = float.fromhex(text)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Invalid hexadecimal cache float.") from exc
    if not isfinite(number) or number.hex() != text:
        raise ValueError("Non-canonical or non-finite cache float.")
    return number


def _encode_value(value: RuntimeValue) -> dict[str, object]:
    if isinstance(value, str):
        return {"type": "text", "value": value}
    if type(value) is float:
        return {"type": "float", "value": _hex_float(value)}
    if isinstance(value, GeoPoint):
        return {
            "type": "geo_point",
            "latitude": _hex_float(value.latitude),
            "longitude": _hex_float(value.longitude),
        }
    if isinstance(value, EntityRef):
        return {"type": "entity_ref", "source": value.source, "entity_id": value.entity_id}
    if isinstance(value, Quantity):
        return {"type": "quantity", "value": _hex_float(value.value), "unit": value.unit}
    raise ValueError("Unsupported cache runtime value type.")


def _decode_value(value: object) -> RuntimeValue:
    if not isinstance(value, dict):
        raise ValueError("Cache value must have an explicit type discriminator.")

    tag = _required_string(value.get("type"))
    if tag == "text":
        obj = _required_object(value, frozenset({"type", "value"}))
        return _required_string(obj["value"])
    if tag == "float":
        obj = _required_object(value, frozenset({"type", "value"}))
        return _parse_hex_float(obj["value"])
    if tag == "geo_point":
        obj = _required_object(value, frozenset({"type", "latitude", "longitude"}))
        return GeoPoint(
            latitude=_parse_hex_float(obj["latitude"]),
            longitude=_parse_hex_float(obj["longitude"]),
        )
    if tag == "entity_ref":
        obj = _required_object(value, frozenset({"type", "source", "entity_id"}))
        return EntityRef(
            source=_required_string(obj["source"]),
            entity_id=_required_string(obj["entity_id"]),
        )
    if tag == "quantity":
        obj = _required_object(value, frozenset({"type", "value", "unit"}))
        return Quantity(
            value=_parse_hex_float(obj["value"]),
            unit=_required_string(obj["unit"]),
        )
    raise ValueError("Unsupported cache value type discriminator.")


def serialize_success(result: OperationResult) -> str:
    """Serialize only valid successful results and their original ordered Evidence."""
    if result.status != "success" or result.value is None:
        raise ValueError("Only successful operations may be cached.")

    payload = {
        "format": RESULT_FORMAT_VERSION,
        "status": "success",
        "value": _encode_value(result.value),
        "evidence": [
            {"source": item.source, "reference": item.reference} for item in result.evidence
        ],
    }
    return dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def deserialize_success(payload: str) -> OperationResult:
    """Restore a fully validated typed result; reject invalid or unknown versions."""
    try:
        document: object = loads(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("Malformed cached result JSON.") from exc

    obj = _required_object(
        document, frozenset({"format", "status", "value", "evidence"})
    )
    if obj["format"] != RESULT_FORMAT_VERSION or obj["status"] != "success":
        raise ValueError("Incompatible or unsuccessful cached result.")
    raw_evidence = obj["evidence"]
    if not isinstance(raw_evidence, list):
        raise ValueError("Cached Evidence must be an ordered list.")

    evidence: list[Evidence] = []
    for raw_item in raw_evidence:
        item = _required_object(raw_item, frozenset({"source", "reference"}))
        evidence.append(
            Evidence(
                source=_required_string(item["source"]),
                reference=_required_string(item["reference"]),
            )
        )

    return OperationResult(
        status="success",
        value=_decode_value(obj["value"]),
        evidence=tuple(evidence),
    )
