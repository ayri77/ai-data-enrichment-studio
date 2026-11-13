"""Verify mountain country references against Wikidata ISO country identifiers."""

import re
from collections.abc import MutableMapping
from json import JSONDecodeError

from ai_data_enrichment.execution.models import EntityRef, Evidence, OperationResult
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError
from ai_data_enrichment.providers.wikidata_constants import (
    WIKIDATA_API_URL,
    WIKIDATA_P_COUNTRY,
    WIKIDATA_P_INSTANCE_OF,
    WIKIDATA_P_ISO_COUNTRY_CODE,
)
from ai_data_enrichment.providers.wikidata_parsing import (
    _best_ranked_statements,
    _wikidata_api_failure,
    _wikidata_identity_drift,
)
from ai_data_enrichment.resolution.geography import normalize_country_code
from ai_data_enrichment.resolution.input_errors import unrecognized_country


def parse_country_codes(country: str) -> tuple[str, ...]:
    """Normalize one or more slash-separated countries to ISO codes."""
    parts = [part.strip() for part in country.split("/")]

    # Both are data problems of one row, not defects of the plan or the run.
    if not parts or any(not part for part in parts):
        raise unrecognized_country()

    try:
        codes = {normalize_country_code(part) for part in parts}
    except LookupError as exc:
        raise unrecognized_country() from exc

    return tuple(sorted(codes))


def referenced_countries(claims: dict[str, object]) -> list[str] | OperationResult:
    """Read the best-ranked P17 country references of a candidate, in order."""

    def failure(code: str, message: str) -> OperationResult:
        return OperationResult(status="failure", error_code=code, message=message)

    if WIKIDATA_P_COUNTRY not in claims:
        return failure(
            "NOT_FOUND",
            f"Mountain country property {WIKIDATA_P_COUNTRY} is missing.",
        )

    statements = claims[WIKIDATA_P_COUNTRY]

    if not isinstance(statements, list):
        return failure(
            "INVALID_RESPONSE",
            f"Wikidata {WIKIDATA_P_COUNTRY} must be a list.",
        )

    country_ids: list[str] = []

    for statement in _best_ranked_statements(statements):
        if not isinstance(statement, dict):
            return failure(
                "INVALID_RESPONSE",
                f"Invalid Wikidata {WIKIDATA_P_COUNTRY} statement.",
            )

        mainsnak = statement.get("mainsnak")

        if not isinstance(mainsnak, dict):
            return failure(
                "INVALID_RESPONSE",
                f"Invalid Wikidata {WIKIDATA_P_COUNTRY} snak.",
            )

        if mainsnak.get("snaktype") != "value":
            continue

        datavalue = mainsnak.get("datavalue")

        if not isinstance(datavalue, dict) or datavalue.get("type") != "wikibase-entityid":
            return failure(
                "INVALID_RESPONSE",
                f"Invalid Wikidata {WIKIDATA_P_COUNTRY} entity reference.",
            )

        value = datavalue.get("value")
        country_qid = value.get("id") if isinstance(value, dict) else None

        if not isinstance(country_qid, str) or re.fullmatch(r"Q[1-9]\d*", country_qid) is None:
            return failure(
                "INVALID_RESPONSE",
                "Invalid country entity ID.",
            )

        if country_qid not in country_ids:
            country_ids.append(country_qid)

    if not country_ids:
        return failure(
            "NOT_FOUND",
            "No verifiable mountain country references.",
        )

    return country_ids


def fetch_country_codes(
    transport: JsonTransport, country_ids: list[str]
) -> dict[str, tuple[str, ...]] | OperationResult:
    """Read the ISO codes (P297) of country entities in one request."""

    def failure(code: str, message: str) -> OperationResult:
        return OperationResult(status="failure", error_code=code, message=message)

    try:
        payload = transport.get_json(
            url=WIKIDATA_API_URL,
            params={
                "action": "wbgetentities",
                "ids": "|".join(country_ids),
                "props": "claims",
                "format": "json",
            },
            timeout_seconds=10.0,
        )
    except (JsonTransportError, JSONDecodeError, OSError) as exc:
        return transport_failure_result(
            exc,
            provider_label="Wikidata API",
        )

    api_failure = _wikidata_api_failure(payload)

    if api_failure is not None:
        return api_failure

    if not isinstance(payload, dict):
        return failure(
            "INVALID_RESPONSE",
            "Invalid Wikidata country response.",
        )

    entities = payload.get("entities")

    if not isinstance(entities, dict):
        return failure(
            "INVALID_RESPONSE",
            "Missing Wikidata country entities.",
        )

    found: dict[str, tuple[str, ...]] = {}

    for country_qid in country_ids:
        entity_data = entities.get(country_qid)

        if not isinstance(entity_data, dict):
            return failure(
                "INVALID_RESPONSE",
                "Invalid Wikidata country entity.",
            )

        identity_issue = _wikidata_identity_drift(
            entity_data,
            requested_qid=country_qid,
            incomplete_code="INCOMPLETE_VERIFICATION",
        )
        if identity_issue is not None:
            return identity_issue

        country_claims = entity_data.get("claims")

        if not isinstance(country_claims, dict):
            return failure(
                "INVALID_RESPONSE",
                "Invalid Wikidata country claims.",
            )

        iso_codes: list[str] = []
        codes = country_claims.get(WIKIDATA_P_ISO_COUNTRY_CODE, [])

        if not isinstance(codes, list):
            return failure(
                "INVALID_RESPONSE",
                f"Wikidata {WIKIDATA_P_ISO_COUNTRY_CODE} must be a list.",
            )

        for statement in _best_ranked_statements(codes):
            if not isinstance(statement, dict):
                return failure(
                    "INVALID_RESPONSE",
                    f"Invalid Wikidata {WIKIDATA_P_ISO_COUNTRY_CODE} statement.",
                )

            mainsnak = statement.get("mainsnak")

            if not isinstance(mainsnak, dict):
                return failure(
                    "INVALID_RESPONSE",
                    f"Invalid Wikidata {WIKIDATA_P_ISO_COUNTRY_CODE} snak.",
                )

            if mainsnak.get("snaktype") != "value":
                continue

            datavalue = mainsnak.get("datavalue")

            if not isinstance(datavalue, dict) or datavalue.get("type") != "string":
                return failure(
                    "INVALID_RESPONSE",
                    "Invalid Wikidata ISO country code.",
                )

            iso_code = datavalue.get("value")

            if not isinstance(iso_code, str) or re.fullmatch(r"[A-Z]{2}", iso_code) is None:
                return failure(
                    "INVALID_RESPONSE",
                    "Malformed Wikidata ISO country code.",
                )

            iso_codes.append(iso_code)

        found[country_qid] = tuple(iso_codes)

    return found


def verify_country(
    transport: JsonTransport,
    *,
    qid: str,
    claims: dict[str, object],
    expected_country_code: str,
    accepted_country_codes: tuple[str, ...] | None = None,
    known_country_codes: MutableMapping[str, tuple[str, ...]] | None = None,
) -> OperationResult:
    """Verify mountain country through P17 and country P297 claims.

    ``known_country_codes`` remembers the ISO codes of country entities that
    were already read, so a table of namesakes in a handful of countries asks
    about each country once, not once per namesake. What a country's code is
    does not depend on which mountain refers to it.
    """

    allowed_codes = (
        accepted_country_codes if accepted_country_codes is not None else (expected_country_code,)
    )
    known = known_country_codes if known_country_codes is not None else {}

    country_ids = referenced_countries(claims)

    if isinstance(country_ids, OperationResult):
        return country_ids

    unknown = [country_qid for country_qid in country_ids if country_qid not in known]

    if unknown:
        # Fetch all not yet known country entities in one request.
        fetched = fetch_country_codes(transport, unknown)

        if isinstance(fetched, OperationResult):
            return fetched

        known.update(fetched)

    for country_qid in country_ids:
        if any(iso_code in allowed_codes for iso_code in known[country_qid]):
            return OperationResult(
                status="success",
                value=EntityRef(
                    source="wikidata",
                    entity_id=qid,
                ),
                evidence=(
                    Evidence(
                        source="wikidata",
                        reference=(
                            f"{qid}:{WIKIDATA_P_INSTANCE_OF},{WIKIDATA_P_COUNTRY};"
                            f"{country_qid}:{WIKIDATA_P_ISO_COUNTRY_CODE}"
                        ),
                    ),
                ),
            )

    return OperationResult(
        status="failure",
        error_code="NOT_FOUND",
        message="Requested country could not be verified for the mountain.",
    )
