"""Wikidata provider handlers for mountain resolution and elevation lookup."""

import re
from collections.abc import Mapping
from json import JSONDecodeError

import pycountry

from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    OperationResult,
    RuntimeValue,
)
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError
from ai_data_enrichment.providers.wikidata_constants import (
    WIKIDATA_API_URL,
    WIKIDATA_CLASS_MAX_HOPS,
    WIKIDATA_CLASS_MAX_NODES,
    WIKIDATA_MAX_SEARCH_PAGES,
    WIKIDATA_P_COUNTRY,
    WIKIDATA_P_ELEVATION,
    WIKIDATA_P_INSTANCE_OF,
    WIKIDATA_P_ISO_COUNTRY_CODE,
    WIKIDATA_P_SUBCLASS_OF,
    WIKIDATA_Q_METRE,
    WIKIDATA_Q_MOUNTAIN,
    WIKIDATA_SEARCH_PAGE_SIZE,
)
from ai_data_enrichment.providers.wikidata_country import verify_country
from ai_data_enrichment.providers.wikidata_ontology import mountain_class_path
from ai_data_enrichment.providers.wikidata_parsing import (
    _best_ranked_statements,
    _candidate_matches_name,
    _parse_elevation_quantity,
    _ranked_entity_ids,
    _wikidata_api_failure,
)

# Stable public imports from the original providers.wikidata module.
__all__ = [
    "WikidataElevationHandler",
    "WikidataMountainResolver",
    "WIKIDATA_API_URL",
    "WIKIDATA_CLASS_MAX_HOPS",
    "WIKIDATA_CLASS_MAX_NODES",
    "WIKIDATA_MAX_SEARCH_PAGES",
    "WIKIDATA_P_COUNTRY",
    "WIKIDATA_P_ELEVATION",
    "WIKIDATA_P_INSTANCE_OF",
    "WIKIDATA_P_ISO_COUNTRY_CODE",
    "WIKIDATA_P_SUBCLASS_OF",
    "WIKIDATA_Q_METRE",
    "WIKIDATA_Q_MOUNTAIN",
    "WIKIDATA_SEARCH_PAGE_SIZE",
]


class WikidataElevationHandler:
    """Look up a geographic entity's elevation in Wikidata."""

    def __init__(self, transport: JsonTransport) -> None:
        self._transport = transport

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        """Retrieve a mountain's elevation from Wikidata."""
        entity = inputs.get("entity")

        if not isinstance(entity, EntityRef) or entity.source != "wikidata":
            return OperationResult(
                status="failure",
                error_code="INVALID_INPUT",
                message="Expected a Wikidata EntityRef.",
            )

        if parameters.get("property") != "elevation":
            return OperationResult(
                status="failure",
                error_code="INVALID_PARAMETER",
                message="Only the elevation property is supported.",
            )

        try:
            payload = self._transport.get_json(
                url=WIKIDATA_API_URL,
                params={
                    "action": "wbgetentities",
                    "ids": entity.entity_id,
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
                message="Wikidata returned an invalid response.",
            )

        entities = payload.get("entities")

        if isinstance(entities, dict):
            entity_data = entities.get(entity.entity_id)

            if isinstance(entity_data, dict) and "missing" in entity_data:
                return OperationResult(
                    status="failure",
                    error_code="NOT_FOUND",
                    message="Wikidata elevation entity does not exist.",
                )

        try:
            claims = payload["entities"][entity.entity_id]["claims"]

            if not isinstance(claims, dict):
                return OperationResult(
                    status="failure",
                    error_code="INVALID_RESPONSE",
                    message="Wikidata claims must be an object.",
                )

            if WIKIDATA_P_ELEVATION not in claims:
                return OperationResult(
                    status="failure",
                    error_code="NOT_FOUND",
                    message=f"Elevation property {WIKIDATA_P_ELEVATION} is missing.",
                )

            statements = claims[WIKIDATA_P_ELEVATION]

            if not isinstance(statements, list):
                return OperationResult(
                    status="failure",
                    error_code="INVALID_RESPONSE",
                    message="Elevation statements must be a list.",
                )

            best_statements = _best_ranked_statements(statements)

            if not best_statements:
                return OperationResult(
                    status="failure",
                    error_code="NOT_FOUND",
                    message="No usable elevation statements found.",
                )

            if len(best_statements) != 1:
                return OperationResult(
                    status="failure",
                    error_code="AMBIGUOUS",
                    message="Multiple equally ranked elevation statements.",
                )

            statement = best_statements[0]

            elevation = _parse_elevation_quantity(statement)

            if isinstance(elevation, OperationResult):
                return elevation

        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Wikidata elevation data is malformed.",
            )

        return OperationResult(
            status="success",
            value=elevation,
            evidence=(
                Evidence(
                    source="wikidata",
                    reference=f"{entity.entity_id}:{WIKIDATA_P_ELEVATION}",
                ),
            ),
        )

class WikidataMountainResolver:
    """Resolve mountain names to Wikidata entity references."""

    def __init__(self, transport: JsonTransport) -> None:
        self._transport = transport

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        """Resolve a mountain using its name and country context."""
        name = inputs.get("name")
        country = inputs.get("country")

        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(country, str)
            or not country.strip()
        ):
            return OperationResult(
                status="failure",
                error_code="INVALID_INPUT",
                message="Mountain name and country must be non-empty strings.",
            )

        if parameters.get("entity_type") != "mountain":
            return OperationResult(
                status="failure",
                error_code="INVALID_PARAMETER",
                message="Only mountain entities are supported.",
            )

        try:
            expected_country_code = pycountry.countries.lookup(country.strip()).alpha_2
        except LookupError:
            return OperationResult(
                status="failure",
                error_code="INVALID_INPUT",
                message="Unrecognized country name.",
            )

        candidates: list[object] = []
        cursor: int | None = None

        for _ in range(WIKIDATA_MAX_SEARCH_PAGES):
            search_params = {
                "action": "wbsearchentities",
                "search": name.strip(),
                "language": "en",
                "format": "json",
                "type": "item",
                "limit": str(WIKIDATA_SEARCH_PAGE_SIZE),
            }

            if cursor is not None:
                search_params["continue"] = str(cursor)

            try:
                payload = self._transport.get_json(
                    url=WIKIDATA_API_URL,
                    params=search_params,
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
                    message="Wikidata search returned an invalid response.",
                )

            page_candidates = payload.get("search")

            if not isinstance(page_candidates, list):
                return OperationResult(
                    status="failure",
                    error_code="INVALID_RESPONSE",
                    message="Wikidata search results must be a list.",
                )

            candidates.extend(page_candidates)

            # No continuation means that the search is complete.
            if "search-continue" not in payload:
                break

            next_cursor = payload["search-continue"]

            if type(next_cursor) is not int or next_cursor <= (cursor if cursor is not None else 0):
                return OperationResult(
                    status="failure",
                    error_code="INVALID_RESPONSE",
                    message="Invalid Wikidata search continuation.",
                )

            cursor = next_cursor

        else:
            # Do not claim uniqueness with unchecked search pages.
            return OperationResult(
                status="failure",
                error_code="INCOMPLETE_SEARCH",
                message=(
                    "Wikidata search exceeded the page limit; "
                    "candidate uniqueness cannot be confirmed."
                ),
            )

        if not candidates:
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="No mountain candidates found in Wikidata.",
            )

        # Track the verified candidate and avoid duplicates.
        verified: OperationResult | None = None
        seen_ids: set[str] = set()

        # Verify each candidate in turn.
        for candidate in candidates:
            if not isinstance(candidate, dict):
                return OperationResult(
                    status="failure",
                    error_code="INVALID_RESPONSE",
                    message="Invalid Wikidata search candidate.",
                )

            qid = candidate.get("id")

            if not isinstance(qid, str) or re.fullmatch(r"Q[1-9]\d*", qid) is None:
                return OperationResult(
                    status="failure",
                    error_code="INVALID_RESPONSE",
                    message="Invalid Wikidata entity ID.",
                )

            # Verify the name before requesting entity details.
            if not _candidate_matches_name(candidate, name):
                continue

            # Search results can contain duplicate entity IDs.
            if qid in seen_ids:
                continue

            seen_ids.add(qid)

            result = self._verify_candidate(
                qid=qid,
                expected_country_code=expected_country_code,
            )

            if result.status == "success":
                if verified is not None:
                    return OperationResult(
                        status="failure",
                        error_code="AMBIGUOUS",
                        message="Multiple verified mountain candidates.",
                    )

                verified = result

            elif result.error_code != "NOT_FOUND":
                # Do not return a potentially false unique match
                # when another candidate could not be verified.
                return result

        if verified is not None:
            return verified

        return OperationResult(
            status="failure",
            error_code="NOT_FOUND",
            message="No matching mountain found in the requested country.",
        )

    def _verify_candidate(
        self,
        *,
        qid: str,
        expected_country_code: str,
    ) -> OperationResult:
        """Verify one candidate's entity type and country."""

        try:
            details = self._transport.get_json(
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

        class_path = mountain_class_path(self._transport, class_ids)

        if isinstance(class_path, OperationResult):
            return class_path

        if class_path is None:
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="Candidate is not classified as a mountain.",
            )

        country_result = verify_country(
            self._transport,
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
