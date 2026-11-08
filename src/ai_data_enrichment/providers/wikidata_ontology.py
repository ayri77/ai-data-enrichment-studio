"""Bounded Wikidata P279 class hierarchy traversal."""

from json import JSONDecodeError

from ai_data_enrichment.execution.models import OperationResult
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError
from ai_data_enrichment.providers.wikidata_constants import (
    WIKIDATA_API_URL,
    WIKIDATA_CLASS_MAX_HOPS,
    WIKIDATA_CLASS_MAX_NODES,
    WIKIDATA_P_SUBCLASS_OF,
    WIKIDATA_Q_MOUNTAIN,
)
from ai_data_enrichment.providers.wikidata_parsing import (
    _ranked_entity_ids,
    _wikidata_api_failure,
    _wikidata_identity_drift,
)


def mountain_class_path(
    transport: JsonTransport,
    class_ids: set[str],
) -> tuple[str, ...] | OperationResult | None:
    """Find a bounded P279 path from a class to mountain."""

    frontier: dict[str, tuple[str, ...]] = {qid: (qid,) for qid in sorted(class_ids)}
    visited: set[str] = set()

    for depth in range(WIKIDATA_CLASS_MAX_HOPS + 1):
        if WIKIDATA_Q_MOUNTAIN in frontier:
            return frontier[WIKIDATA_Q_MOUNTAIN]

        pending = {qid: path for qid, path in frontier.items() if qid not in visited}

        if not pending:
            return None

        if (
            depth == WIKIDATA_CLASS_MAX_HOPS
            or len(visited) + len(pending) > WIKIDATA_CLASS_MAX_NODES
        ):
            return OperationResult(
                status="failure",
                error_code="INCOMPLETE_HIERARCHY",
                message="Wikidata class traversal budget exceeded.",
            )

        class_batch = sorted(pending)
        visited.update(class_batch)

        try:
            payload = transport.get_json(
                url=WIKIDATA_API_URL,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(class_batch),
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
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Invalid Wikidata class response.",
            )

        entities = payload.get("entities")

        if not isinstance(entities, dict):
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Missing Wikidata class entities.",
            )

        next_frontier: dict[str, tuple[str, ...]] = {}

        for class_qid in class_batch:
            entity = entities.get(class_qid)

            if not isinstance(entity, dict):
                return OperationResult(
                    status="failure",
                    error_code="INCOMPLETE_HIERARCHY",
                    message="Wikidata class entity is unavailable.",
                )

            if "missing" in entity:
                return OperationResult(
                    status="failure",
                    error_code="INCOMPLETE_HIERARCHY",
                    message="Referenced Wikidata class is missing.",
                )

            identity_issue = _wikidata_identity_drift(
                entity,
                requested_qid=class_qid,
                incomplete_code="INCOMPLETE_HIERARCHY",
            )
            if identity_issue is not None:
                return identity_issue

            claims = entity.get("claims")

            if not isinstance(claims, dict):
                return OperationResult(
                    status="failure",
                    error_code="INVALID_RESPONSE",
                    message="Invalid Wikidata class claims.",
                )

            statements = claims.get(WIKIDATA_P_SUBCLASS_OF, [])

            if not isinstance(statements, list):
                return OperationResult(
                    status="failure",
                    error_code="INVALID_RESPONSE",
                    message="Wikidata P279 must be a list.",
                )

            parent_ids = _ranked_entity_ids(
                statements,
                property_id=WIKIDATA_P_SUBCLASS_OF,
            )

            if isinstance(parent_ids, OperationResult):
                return parent_ids

            for parent_qid in sorted(parent_ids):
                if parent_qid not in visited and parent_qid not in next_frontier:
                    next_frontier[parent_qid] = (
                        *pending[class_qid],
                        parent_qid,
                    )

        frontier = next_frontier

    return None
