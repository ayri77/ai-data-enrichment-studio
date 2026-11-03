"""Wikidata candidate semantic verification: P31/P279 type and P17 country.

A single direct film type (P31=Q11424) is rejected without climbing the
film ontology. Unknown classes still require the bounded hierarchy walk.
"""

from json import JSONDecodeError

from ai_data_enrichment.execution.models import Evidence, OperationResult
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError
from ai_data_enrichment.providers.wikidata_constants import (
    WIKIDATA_API_URL,
    WIKIDATA_DIRECT_NON_MOUNTAIN_CLASSES,
    WIKIDATA_P_INSTANCE_OF,
    WIKIDATA_P_SUBCLASS_OF,
)
from ai_data_enrichment.providers.wikidata_country import verify_country
from ai_data_enrichment.providers.wikidata_ontology import mountain_class_path
from ai_data_enrichment.providers.wikidata_parsing import (
    _ranked_entity_ids,
    _wikidata_api_failure,
)


def verify_wikidata_mountain_candidate(
    transport: JsonTransport,
    *,
    qid: str,
    expected_country_code: str,
) -> OperationResult:
    """Verify one candidate's entity type and country."""

    try:
        details = transport.get_json(
            url=WIKIDATA_API_URL,
            params={
                "action": "wbgetentities",
                "ids": qid,
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

    api_failure = _wikidata_api_failure(details)

    if api_failure is not None:
        return api_failure

    if not isinstance(details, dict):
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Wikidata entity response must be an object.",
        )

    entities = details.get("entities")

    if not isinstance(entities, dict):
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Wikidata entities must be an object.",
        )

    entity_data = entities.get(qid)

    if not isinstance(entity_data, dict):
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Wikidata entity data is missing or invalid.",
        )

    if "missing" in entity_data:
        return OperationResult(
            status="failure",
            error_code="NOT_FOUND",
            message="Wikidata candidate entity does not exist.",
        )

    claims = entity_data.get("claims")

    if not isinstance(claims, dict):
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Wikidata entity claims must be an object.",
        )

    instance_of = claims.get(WIKIDATA_P_INSTANCE_OF, [])

    if not isinstance(instance_of, list):
        return OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message="Wikidata P31 must be a list.",
        )

    class_ids = _ranked_entity_ids(
        instance_of,
        property_id=WIKIDATA_P_INSTANCE_OF,
    )

    if isinstance(class_ids, OperationResult):
        return class_ids

    # Reject only a direct, curated, incompatible ontology class.
    # Unknown parallel P31 types still require bounded ontology verification.
    if class_ids and class_ids.issubset(WIKIDATA_DIRECT_NON_MOUNTAIN_CLASSES):
        return OperationResult(
            status="failure",
            error_code="NOT_FOUND",
            message="Candidate has an explicitly non-mountain entity type.",
        )

    class_path = mountain_class_path(transport, class_ids)

    if isinstance(class_path, OperationResult):
        return class_path

    if class_path is None:
        return OperationResult(
            status="failure",
            error_code="NOT_FOUND",
            message="Candidate is not classified as a mountain.",
        )

    country_result = verify_country(
        transport,
        qid=qid,
        claims=claims,
        expected_country_code=expected_country_code,
    )

    if country_result.status != "success":
        return country_result

    # Record the subclass chain when an indirect type matched.
    if len(class_path) > 1:
        subclass_evidence = Evidence(
            source="wikidata",
            reference=(
                f"{qid}:{WIKIDATA_P_INSTANCE_OF}={class_path[0]};"
                f"{WIKIDATA_P_SUBCLASS_OF}=" + "->".join(class_path)
            ),
        )

        return OperationResult(
            status="success",
            value=country_result.value,
            evidence=(
                *country_result.evidence,
                subclass_evidence,
            ),
        )

    return country_result
