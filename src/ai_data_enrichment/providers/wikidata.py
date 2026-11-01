"""Wikidata provider handlers."""

import re
from collections.abc import Mapping
from json import JSONDecodeError
from math import isfinite

import pycountry

from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError

WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_SEARCH_PAGE_SIZE = 50
WIKIDATA_MAX_SEARCH_PAGES = 3

# Wikidata semantic identifiers.
WIKIDATA_P_INSTANCE_OF = "P31"
WIKIDATA_P_SUBCLASS_OF = "P279"
WIKIDATA_P_COUNTRY = "P17"
WIKIDATA_P_ISO_COUNTRY_CODE = "P297"
WIKIDATA_P_ELEVATION = "P2044"

WIKIDATA_Q_MOUNTAIN = "Q8502"
WIKIDATA_Q_METRE = "Q11573"

# Maximum subclass links and unique class nodes to inspect.
WIKIDATA_CLASS_MAX_HOPS = 3
WIKIDATA_CLASS_MAX_NODES = 24


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
    """Extract valid entity IDs from best-ranked statements."""

    entity_ids: set[str] = set()

    for statement in _best_ranked_statements(statements):
        mainsnak = statement.get("mainsnak")

        if not isinstance(mainsnak, dict):
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message=f"Invalid {property_id} statement structure.",
            )

        if mainsnak.get("snaktype") != "value":
            continue

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

    def _verify_country(
        self,
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
            payload = self._transport.get_json(
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

    def _mountain_class_path(
        self,
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
                payload = self._transport.get_json(
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

                claims = entity.get("claims", {})

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

        class_path = self._mountain_class_path(class_ids)

        if isinstance(class_path, OperationResult):
            return class_path

        if class_path is None:
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="Candidate is not classified as a mountain.",
            )

        country_result = self._verify_country(
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
