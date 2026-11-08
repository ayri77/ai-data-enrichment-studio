"""Pure Wikidata response parsing and statement validation helpers."""

import re
from math import isfinite

from ai_data_enrichment.execution.models import OperationResult, Quantity
from ai_data_enrichment.providers.wikidata_constants import WIKIDATA_Q_METRE


def _wikidata_identity_drift(
    entity: dict[str, object],
    *,
    requested_qid: str,
    incomplete_code: str,
) -> OperationResult | None:
    """Reject changed entity IDs from redirects instead of misattributing Evidence.

    wbgetentities may key a redirected response by the original requested QID
    while its returned entity's own 'id' is different. Until redirects are
    explicitly resolved, a mismatched ID cannot verify the requested entity.
    An omitted ID is tolerated for backwards-compatible minimal fixtures.
    """
    if "id" not in entity:
        return None
    actual = entity["id"]
    if not isinstance(actual, str) or re.fullmatch(r"Q[1-9]\d*", actual) is None:
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Wikidata entity contains an invalid ID.",
        )
    if actual != requested_qid:
        return OperationResult(
            status="failure",
            error_code=incomplete_code,
            message="Wikidata returned a different entity ID (possible redirect).",
        )
    return None


def _normalize_entity_name(value: str) -> str:
    """Normalize entity names for case-insensitive exact matching."""
    return " ".join(value.split()).casefold()


def _candidate_matches_name(
    candidate: dict[str, object],
    requested_name: str,
) -> bool:
    """Check a Wikidata search label, matched term, or alias."""

    terms: list[str] = []

    label = candidate.get("label")

    if isinstance(label, str):
        terms.append(label)

    match = candidate.get("match")

    if isinstance(match, dict):
        matched_text = match.get("text")

        if isinstance(matched_text, str):
            terms.append(matched_text)

    aliases = candidate.get("aliases")

    if isinstance(aliases, list):
        terms.extend(alias for alias in aliases if isinstance(alias, str))

    normalized_name = _normalize_entity_name(requested_name)

    return any(_normalize_entity_name(term) == normalized_name for term in terms)


def _wikidata_api_failure(payload: object) -> OperationResult | None:
    """Detect explicit Wikidata Action API error responses."""

    if not isinstance(payload, dict):
        return None

    if "error" in payload:
        error = payload["error"]

        if not isinstance(error, dict):
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Malformed Wikidata API error envelope.",
            )

        code = error.get("code")

        if not isinstance(code, str) or not code.strip():
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Missing Wikidata API error code.",
            )

        return OperationResult(
            status="failure",
            error_code="PROVIDER_ERROR",
            message=f"Wikidata API returned error code: {code}.",
        )

    if payload.get("success") == 0:
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Wikidata reported failure without an error code.",
        )

    return None


def _best_ranked_statements(
    statements: list[object],
) -> list[dict[str, object]]:
    """Return statements with the highest usable Wikidata rank."""
    rank_priority = {"normal": 1, "preferred": 2}

    ranked: list[tuple[int, dict[str, object]]] = []

    for item in statements:
        if not isinstance(item, dict):
            continue

        rank = item.get("rank")

        if isinstance(rank, str) and rank in rank_priority:
            ranked.append((rank_priority[rank], item))

    if not ranked:
        return []

    best_priority = max(priority for priority, _ in ranked)

    return [item for priority, item in ranked if priority == best_priority]


def _ranked_entity_ids(
    statements: list[object],
    *,
    property_id: str,
) -> set[str] | OperationResult:
    """Extract complete best-ranked references without hiding malformed/unknown types."""

    for statement in statements:
        if not isinstance(statement, dict) or statement.get("rank") not in (
            "preferred",
            "normal",
            "deprecated",
        ):
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message=f"Invalid {property_id} statement rank or structure.",
            )

    entity_ids: set[str] = set()

    for statement in _best_ranked_statements(statements):
        mainsnak = statement.get("mainsnak")

        if not isinstance(mainsnak, dict):
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message=f"Invalid {property_id} statement structure.",
            )

        if mainsnak.get("snaktype") in ("somevalue", "novalue"):
            return OperationResult(
                status="failure",
                error_code="INCOMPLETE_VERIFICATION",
                message=f"Unknown or non-value best-ranked {property_id} reference.",
            )
        if (
            mainsnak.get("snaktype") != "value"
            or mainsnak.get("property", property_id) != property_id
        ):
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message=f"Invalid {property_id} snak type or property.",
            )

        datavalue = mainsnak.get("datavalue")

        if not isinstance(datavalue, dict) or datavalue.get("type") != "wikibase-entityid":
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message=f"Invalid {property_id} entity reference.",
            )

        value = datavalue.get("value")
        qid = value.get("id") if isinstance(value, dict) else None

        if not isinstance(qid, str) or re.fullmatch(r"Q[1-9]\d*", qid) is None:
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message=f"Invalid {property_id} entity ID.",
            )

        entity_ids.add(qid)

    return entity_ids


def _parse_elevation_quantity(
    statement: dict[str, object],
) -> Quantity | OperationResult:
    """Validate and normalize a Wikidata elevation statement."""

    mainsnak = statement["mainsnak"]

    if not isinstance(mainsnak, dict) or mainsnak.get("snaktype") != "value":
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Invalid elevation snak type.",
        )

    datavalue = mainsnak["datavalue"]

    if not isinstance(datavalue, dict) or datavalue.get("type") != "quantity":
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Elevation must be a quantity.",
        )

    quantity = datavalue["value"]

    if not isinstance(quantity, dict):
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Invalid elevation quantity structure.",
        )

    raw_amount = quantity["amount"]

    if not isinstance(raw_amount, str):
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Elevation amount must be a string.",
        )

    amount = float(raw_amount)
    unit = quantity["unit"]

    if (
        not isinstance(unit, str)
        or re.fullmatch(
            r"https?://www\.wikidata\.org/entity/Q[1-9]\d*",
            unit,
        )
        is None
    ):
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Invalid Wikidata elevation unit URI.",
        )

    if unit != f"http://www.wikidata.org/entity/{WIKIDATA_Q_METRE}":
        return OperationResult(
            status="failure",
            error_code="UNSUPPORTED_UNIT",
            message="Elevation is not expressed in metres.",
        )

    if not isfinite(amount):
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Elevation must be finite.",
        )

    return Quantity(value=amount, unit="m")
