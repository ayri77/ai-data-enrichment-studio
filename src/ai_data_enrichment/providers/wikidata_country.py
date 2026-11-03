"""Verify mountain country references against Wikidata ISO country identifiers."""

import re
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
    _wikidata_identity_drift,
    _wikidata_api_failure,
)


def verify_country(
    transport: JsonTransport,
    *,
    qid: str,
    claims: dict[str, object],
    expected_country_code: str,
) -> OperationResult:
    """Verify mountain country through P17 and country P297 claims."""

    def failure(code: str, message: str) -> OperationResult:
        return OperationResult(
            status="failure",
            error_code=code,
            message=message,
        )

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

    # Fetch all referenced country entities in one request.
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

        if WIKIDATA_P_ISO_COUNTRY_CODE not in country_claims:
            continue

        codes = country_claims[WIKIDATA_P_ISO_COUNTRY_CODE]

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

            if iso_code == expected_country_code:
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

    return failure(
        "NOT_FOUND",
        "Requested country could not be verified for the mountain.",
    )
