"""Wikidata candidate semantic verification: P31/P279 type and P17 country.

Mountain type and country must both be positively verified.
Fetched claims are reused; only indirect types require a bounded hierarchy walk.
"""

from collections.abc import Mapping
from json import JSONDecodeError

from ai_data_enrichment.execution.models import Evidence, OperationResult
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError
from ai_data_enrichment.providers.wikidata_constants import (
    WIKIDATA_API_URL,
    WIKIDATA_ENTITY_BATCH_SIZE,
    WIKIDATA_P_COUNTRY,
    WIKIDATA_P_INSTANCE_OF,
    WIKIDATA_P_SUBCLASS_OF,
)
from ai_data_enrichment.providers.wikidata_country import verify_country
from ai_data_enrichment.providers.wikidata_ontology import mountain_class_path
from ai_data_enrichment.providers.wikidata_parsing import (
    _ranked_entity_ids,
    _wikidata_api_failure,
    _wikidata_identity_drift,
)


def fetch_wikidata_candidate_entities(
    transport: JsonTransport, *, qids: tuple[str, ...]
) -> dict[str, object] | OperationResult:
    """Fetch bounded claim batches once for candidate verification."""
    results: dict[str, object] = {}
    for offset in range(0, len(qids), WIKIDATA_ENTITY_BATCH_SIZE):
        batch = qids[offset : offset + WIKIDATA_ENTITY_BATCH_SIZE]
        try:
            details = transport.get_json(
                url=WIKIDATA_API_URL,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
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

        results.update({qid: entities.get(qid) for qid in batch})
    return results


def needs_provider_verification(entity_data: object) -> bool:
    """Whether verifying this already-fetched entity can require further requests.

    A candidate without a recorded class or without a country is decided from the
    fetched claims alone (it cannot be positively eligible), so it must not use up
    the budget meant for candidates whose class and country have to be looked up.
    Anything unusual counts as needing the provider, which only costs budget.
    """
    if not isinstance(entity_data, dict) or "missing" in entity_data:
        return False
    claims = entity_data.get("claims")
    if not isinstance(claims, dict):
        return False
    return bool(claims.get(WIKIDATA_P_INSTANCE_OF)) and WIKIDATA_P_COUNTRY in claims


def verify_wikidata_mountain_candidate(
    transport: JsonTransport,
    *,
    qid: str,
    expected_country_code: str,
    accepted_country_codes: tuple[str, ...] | None = None,
    entities: Mapping[str, object] | None = None,
) -> OperationResult:
    """Verify one candidate's entity type and country."""

    if entities is None:
        fetched = fetch_wikidata_candidate_entities(transport, qids=(qid,))
        if isinstance(fetched, OperationResult):
            return fetched
        entities = fetched
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
            error_code="SOURCE_ENTITY_MISSING",
            message="Wikidata candidate entity does not exist.",
        )

    identity_issue = _wikidata_identity_drift(
        entity_data,
        requested_qid=qid,
        incomplete_code="INCOMPLETE_VERIFICATION",
    )
    if identity_issue is not None:
        return identity_issue

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

    if not class_ids:
        return OperationResult(
            status="failure",
            error_code="UNVERIFIED_TYPE",
            message="Candidate has no verifiable P31 class.",
        )

    # Without geographic context the candidate cannot be positively eligible.
    if WIKIDATA_P_COUNTRY not in claims:
        return OperationResult(
            status="failure",
            error_code="NOT_FOUND",
            message="Candidate has no verifiable country property.",
        )

    class_path = mountain_class_path(transport, class_ids)

    if isinstance(class_path, OperationResult):
        return class_path

    if class_path is None:
        return OperationResult(
            status="failure",
            error_code="NON_MOUNTAIN_CLASS",
            message="Candidate is not classified as a mountain.",
        )

    country_result = verify_country(
        transport,
        qid=qid,
        claims=claims,
        expected_country_code=expected_country_code,
        accepted_country_codes=accepted_country_codes,
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
