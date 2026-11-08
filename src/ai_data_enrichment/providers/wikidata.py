"""Wikidata provider handlers for mountain resolution and elevation lookup."""

import re
from collections.abc import Mapping
from json import JSONDecodeError

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
    WIKIDATA_MAX_VERIFY_CANDIDATES,
    WIKIDATA_P_COUNTRY,
    WIKIDATA_P_ELEVATION,
    WIKIDATA_P_INSTANCE_OF,
    WIKIDATA_P_ISO_COUNTRY_CODE,
    WIKIDATA_P_SUBCLASS_OF,
    WIKIDATA_Q_METRE,
    WIKIDATA_Q_MOUNTAIN,
    WIKIDATA_SEARCH_PAGE_SIZE,
)
from ai_data_enrichment.providers.wikidata_country import parse_country_codes
from ai_data_enrichment.providers.wikidata_parsing import (
    _best_ranked_statements,
    _candidate_matches_name,
    _parse_elevation_quantity,
    _wikidata_api_failure,
    _wikidata_identity_drift,
)
from ai_data_enrichment.providers.wikidata_search import fetch_wikidata_search
from ai_data_enrichment.providers.wikidata_verification import verify_wikidata_mountain_candidate

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
            if isinstance(entity_data, dict):
                identity_issue = _wikidata_identity_drift(
                    entity_data,
                    requested_qid=entity.entity_id,
                    incomplete_code="INCOMPLETE_VERIFICATION",
                )
                if identity_issue is not None:
                    return identity_issue

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
            accepted_country_codes = parse_country_codes(country)
        except ValueError:
            return OperationResult(
                status="failure",
                error_code="INVALID_INPUT",
                message="Unrecognized mountain country context.",
            )

        expected_country_code = accepted_country_codes[0]

        search = fetch_wikidata_search(self._transport, name=name)
        if search.failure is not None:
            return search.failure
        if not search.complete:
            # Preserve strict legacy semantics; the paired-report service can
            # expose the partial candidate list for later user clarification.
            return OperationResult(
                status="failure",
                error_code="INCOMPLETE_SEARCH",
                message=(
                    "Wikidata search exceeded the page limit; "
                    "candidate uniqueness cannot be confirmed."
                ),
            )
        candidates = search.items

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

            # Do not spend unbounded requests on search aliases or homonyms.
            # A partial verification cannot establish a unique identity.
            if len(seen_ids) >= WIKIDATA_MAX_VERIFY_CANDIDATES:
                return OperationResult(
                    status="failure",
                    error_code="INCOMPLETE_VERIFICATION",
                    message="Wikidata candidate verification budget exceeded.",
                )
            seen_ids.add(qid)

            result = self._verify_candidate(
                qid=qid,
                expected_country_code=expected_country_code,
                accepted_country_codes=accepted_country_codes,
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
        accepted_country_codes: tuple[str, ...] | None = None,
    ) -> OperationResult:
        """Preserve the strict handler API while using shared source verification."""
        checked = verify_wikidata_mountain_candidate(
            self._transport,
            qid=qid,
            expected_country_code=expected_country_code,
            accepted_country_codes=accepted_country_codes,
        )
        # Preserve the public strict resolver's longstanding NOT_FOUND contract.
        # Structured source-specific reasons are used by the audited service.
        if checked.error_code in {
            "NON_MOUNTAIN_CLASS",
            "SOURCE_ENTITY_MISSING",
            "UNVERIFIED_TYPE",
        }:
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message=checked.message,
            )
        return checked
