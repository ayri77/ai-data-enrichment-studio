"""Offline tests for Wikidata provider handlers."""

from collections.abc import Mapping
from unittest.mock import Mock

import pytest
from tests.support.http import StubJsonTransport
from tests.support.wikidata import entity_statement

from ai_data_enrichment.execution.models import EntityRef, Quantity
from ai_data_enrichment.providers.http_transport import JsonTransportError
from ai_data_enrichment.providers.wikidata import (
    WIKIDATA_API_URL,
    WikidataElevationHandler,
    WikidataMountainResolver,
)


def test_looks_up_mountain_elevation_in_meters() -> None:
    """Resolve a Wikidata elevation statement into a typed quantity."""
    payload = {
        "entities": {
            "Q513": {
                "id": "Q513",
                "claims": {
                    "P2044": [
                        {
                            "rank": "preferred",
                            "mainsnak": {
                                "snaktype": "value",
                                "datavalue": {
                                    "type": "quantity",
                                    "value": {
                                        "amount": "+8848.86",
                                        "unit": "http://www.wikidata.org/entity/Q11573",
                                    },
                                },
                            },
                        },
                    ],
                },
            },
        },
    }

    transport = StubJsonTransport(payload)
    handler = WikidataElevationHandler(transport=transport)

    result = handler.execute(
        inputs={
            "entity": EntityRef(
                source="wikidata",
                entity_id="Q513",
            ),
        },
        parameters={"property": "elevation"},
    )

    assert result.status == "success"
    assert result.value == Quantity(value=8848.86, unit="m")
    assert result.error_code is None
    assert result.evidence
    assert result.evidence[0].source == "wikidata"
    assert "Q513" in result.evidence[0].reference
    assert "P2044" in result.evidence[0].reference


def test_prefers_preferred_elevation_statement() -> None:
    """Select the preferred statement regardless of API result order."""

    def statement(amount: str, rank: str) -> dict:
        return {
            "rank": rank,
            "mainsnak": {
                "snaktype": "value",
                "datavalue": {
                    "type": "quantity",
                    "value": {
                        "amount": amount,
                        "unit": "http://www.wikidata.org/entity/Q11573",
                    },
                },
            },
        }

    payload = {
        "entities": {
            "Q513": {
                "id": "Q513",
                "claims": {
                    "P2044": [
                        statement("+8840", "deprecated"),
                        statement("+8848", "normal"),
                        statement("+8848.86", "preferred"),
                    ],
                },
            },
        },
    }

    handler = WikidataElevationHandler(transport=StubJsonTransport(payload))

    result = handler.execute(
        inputs={
            "entity": EntityRef(
                source="wikidata",
                entity_id="Q513",
            ),
        },
        parameters={"property": "elevation"},
    )

    assert result.status == "success"
    assert result.value == Quantity(value=8848.86, unit="m")


@pytest.mark.parametrize(
    ("statements", "expected_value", "expected_error"),
    [
        (
            [("+8840", "deprecated"), ("+8848", "normal")],
            8848.0,
            None,
        ),
        (
            [("+8840", "deprecated")],
            None,
            "NOT_FOUND",
        ),
        (
            [("+8848.86", "preferred"), ("+8849", "preferred")],
            None,
            "AMBIGUOUS",
        ),
        (
            [("+8848", "normal"), ("+8849", "normal")],
            None,
            "AMBIGUOUS",
        ),
    ],
    ids=[
        "fallback-to-normal",
        "deprecated-only",
        "multiple-preferred",
        "multiple-normal",
    ],
)
def test_elevation_statement_rank_selection(
    statements: list[tuple[str, str]],
    expected_value: float | None,
    expected_error: str | None,
) -> None:
    """Select a unique best-ranked statement or report failure."""

    claims = [
        {
            "rank": rank,
            "mainsnak": {
                "snaktype": "value",
                "datavalue": {
                    "type": "quantity",
                    "value": {
                        "amount": amount,
                        "unit": "http://www.wikidata.org/entity/Q11573",
                    },
                },
            },
        }
        for amount, rank in statements
    ]

    payload = {
        "entities": {
            "Q513": {
                "id": "Q513",
                "claims": {"P2044": claims},
            },
        },
    }

    handler = WikidataElevationHandler(transport=StubJsonTransport(payload))

    result = handler.execute(
        inputs={
            "entity": EntityRef(
                source="wikidata",
                entity_id="Q513",
            ),
        },
        parameters={"property": "elevation"},
    )

    if expected_error is None:
        assert expected_value is not None

        assert result.status == "success"
        assert result.value == Quantity(value=expected_value, unit="m")
        assert result.error_code is None
        assert result.evidence
    else:
        assert result.status == "failure"
        assert result.error_code == expected_error
        assert result.value is None
        assert not result.evidence


@pytest.mark.parametrize(
    ("snaktype", "value_type", "amount"),
    [
        ("novalue", "quantity", "+8848.86"),
        ("value", "string", "+8848.86"),
        ("value", "quantity", True),
    ],
    ids=[
        "invalid-snaktype",
        "invalid-datavalue-type",
        "boolean-amount",
    ],
)
def test_rejects_invalid_elevation_value_structure(
    snaktype: str,
    value_type: str,
    amount: str | bool,
) -> None:
    """Reject elevation statements with invalid Wikidata value metadata."""
    payload = {
        "entities": {
            "Q513": {
                "id": "Q513",
                "claims": {
                    "P2044": [
                        {
                            "rank": "preferred",
                            "mainsnak": {
                                "snaktype": snaktype,
                                "datavalue": {
                                    "type": value_type,
                                    "value": {
                                        "amount": amount,
                                        "unit": "http://www.wikidata.org/entity/Q11573",
                                    },
                                },
                            },
                        },
                    ],
                },
            },
        },
    }

    handler = WikidataElevationHandler(transport=StubJsonTransport(payload))

    result = handler.execute(
        inputs={
            "entity": EntityRef(
                source="wikidata",
                entity_id="Q513",
            ),
        },
        parameters={"property": "elevation"},
    )

    assert result.status == "failure"
    assert result.error_code == "INVALID_RESPONSE"
    assert result.value is None
    assert not result.evidence


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"P2044": []},
    ],
    ids=[
        "missing-elevation-property",
        "empty-elevation-statements",
    ],
)
def test_handles_missing_elevation(
    claims: dict[str, object],
) -> None:
    """Report missing elevation as NOT_FOUND, not malformed data."""
    payload = {
        "entities": {
            "Q513": {
                "id": "Q513",
                "claims": claims,
            },
        },
    }

    transport = StubJsonTransport(payload)
    handler = WikidataElevationHandler(transport=transport)

    result = handler.execute(
        inputs={
            "entity": EntityRef(
                source="wikidata",
                entity_id="Q513",
            ),
        },
        parameters={"property": "elevation"},
    )

    assert result.status == "failure"
    assert result.error_code == "NOT_FOUND"
    assert result.value is None
    assert not result.evidence


@pytest.mark.parametrize(
    "statements",
    [
        {"rank": "preferred"},
        "not-a-list",
        None,
    ],
    ids=[
        "object-instead-of-list",
        "string-instead-of-list",
        "null-instead-of-list",
    ],
)
def test_rejects_invalid_elevation_statements_type(
    statements: object,
) -> None:
    """Reject malformed P2044 collections as invalid responses."""
    payload = {
        "entities": {
            "Q513": {
                "id": "Q513",
                "claims": {
                    "P2044": statements,
                },
            },
        },
    }

    handler = WikidataElevationHandler(transport=StubJsonTransport(payload))

    result = handler.execute(
        inputs={
            "entity": EntityRef(
                source="wikidata",
                entity_id="Q513",
            ),
        },
        parameters={"property": "elevation"},
    )

    assert result.status == "failure"
    assert result.error_code == "INVALID_RESPONSE"
    assert result.value is None
    assert not result.evidence


@pytest.mark.parametrize(
    ("unit", "expected_error"),
    [
        (
            "http://www.wikidata.org/entity/Q3710",
            "UNSUPPORTED_UNIT",
        ),
        (None, "INVALID_RESPONSE"),
        (42, "INVALID_RESPONSE"),
        ("meters", "INVALID_RESPONSE"),
    ],
    ids=[
        "unsupported-unit",
        "null-unit",
        "numeric-unit",
        "invalid-unit-format",
    ],
)
def test_rejects_invalid_or_unsupported_elevation_units(
    unit: object,
    expected_error: str,
) -> None:
    """Distinguish unsupported units from malformed unit metadata."""
    payload = {
        "entities": {
            "Q513": {
                "id": "Q513",
                "claims": {
                    "P2044": [
                        {
                            "rank": "preferred",
                            "mainsnak": {
                                "snaktype": "value",
                                "datavalue": {
                                    "type": "quantity",
                                    "value": {
                                        "amount": "+8848.86",
                                        "unit": unit,
                                    },
                                },
                            },
                        },
                    ],
                },
            },
        },
    }

    handler = WikidataElevationHandler(transport=StubJsonTransport(payload))

    result = handler.execute(
        inputs={
            "entity": EntityRef(
                source="wikidata",
                entity_id="Q513",
            ),
        },
        parameters={"property": "elevation"},
    )

    assert result.status == "failure"
    assert result.error_code == expected_error
    assert result.value is None
    assert not result.evidence


@pytest.mark.parametrize(
    ("transport_error", "expected_error"),
    [
        (
            JsonTransportError("timeout"),
            "PROVIDER_ERROR",
        ),
        (
            JsonTransportError("network"),
            "PROVIDER_ERROR",
        ),
        (
            JsonTransportError(
                "http_status",
                status_code=429,
                retry_after="60",
            ),
            "PROVIDER_ERROR",
        ),
        (
            JsonTransportError("invalid_json"),
            "INVALID_RESPONSE",
        ),
    ],
    ids=[
        "timeout",
        "network",
        "http-status",
        "invalid-json",
    ],
)
def test_handles_wikidata_transport_failures(
    transport_error: JsonTransportError,
    expected_error: str,
) -> None:
    """Map transport failures to standardized operation errors."""
    transport = StubJsonTransport(
        payload={},
        error=transport_error,
    )

    handler = WikidataElevationHandler(transport=transport)

    result = handler.execute(
        inputs={
            "entity": EntityRef(
                source="wikidata",
                entity_id="Q513",
            ),
        },
        parameters={"property": "elevation"},
    )

    assert result.status == "failure"
    assert result.error_code == expected_error
    assert result.value is None
    assert not result.evidence

    assert len(transport.requests) == 1


def test_rejects_blank_mountain_name() -> None:
    """Reject an empty mountain name without calling Wikidata."""
    transport = StubJsonTransport(payload={})
    handler = WikidataMountainResolver(transport=transport)

    result = handler.execute(
        inputs={
            "name": "   ",
            "country": "Nepal",
        },
        parameters={"entity_type": "mountain"},
    )

    assert result.status == "failure"
    assert result.error_code == "INVALID_INPUT"
    assert result.value is None
    assert not result.evidence

    assert transport.requests == []


def test_returns_not_found_when_mountain_search_is_empty() -> None:
    """Return NOT_FOUND when Wikidata search has no candidates."""
    transport = StubJsonTransport(
        payload={"search": []},
    )
    handler = WikidataMountainResolver(transport=transport)

    result = handler.execute(
        inputs={
            "name": "Unknown Mountain",
            "country": "Nepal",
        },
        parameters={"entity_type": "mountain"},
    )

    assert result.status == "failure"
    assert result.error_code == "NOT_FOUND"
    assert result.value is None
    assert not result.evidence

    assert len(transport.requests) == 1

    url, params, timeout = transport.requests[0]

    assert url == WIKIDATA_API_URL
    assert params["action"] == "wbsearchentities"
    assert params["search"] == "Unknown Mountain"
    assert params["language"] == "en"
    assert params["format"] == "json"
    assert timeout == 10.0


def test_rejects_search_result_that_is_not_a_mountain() -> None:
    """Do not trust a search match without verifying its entity type."""
    transport = Mock()

    transport.get_json.side_effect = [
        {
            "search": [
                {
                    "id": "Q42",
                    "label": "Mount Everest",
                },
            ],
        },
        {
            "entities": {
                "Q42": {
                    "id": "Q42",
                    "claims": {
                        "P31": [
                            {
                                "rank": "normal",
                                "mainsnak": {
                                    "snaktype": "value",
                                    "datavalue": {
                                        "type": "wikibase-entityid",
                                        "value": {"id": "Q5"},
                                    },
                                },
                            },
                        ],
                    },
                },
            },
        },
    ]

    handler = WikidataMountainResolver(transport=transport)

    result = handler.execute(
        inputs={
            "name": "Mount Everest",
            "country": "Nepal",
        },
        parameters={"entity_type": "mountain"},
    )

    assert result.status == "failure"
    assert result.error_code == "NOT_FOUND"
    assert result.value is None
    assert not result.evidence

    assert transport.get_json.call_count == 2

    verification_request = transport.get_json.call_args_list[1]
    params = verification_request.kwargs["params"]

    assert params["action"] == "wbgetentities"
    assert params["ids"] == "Q42"
    assert params["props"] == "claims"


@pytest.mark.parametrize(
    ("country", "p17", "country_codes", "expected_error", "expected_calls"),
    [
        ("Nepal", ["Q837"], {"Q837": "NP"}, None, 3),
        ("Nepal", ["Q148"], {"Q148": "CN"}, "NOT_FOUND", 3),
        (
            "China",
            ["Q837", "Q148"],
            {"Q837": "NP", "Q148": "CN"},
            None,
            3,
        ),
        ("Nepal", None, {}, "NOT_FOUND", 2),
        ("Nepal", "invalid", {}, "INVALID_RESPONSE", 2),
    ],
    ids=[
        "matching-country",
        "different-country",
        "mountain-in-two-countries",
        "missing-country-property",
        "malformed-country-property",
    ],
)
def test_verifies_mountain_country(
    country: str,
    p17: list[str] | str | None,
    country_codes: dict[str, str],
    expected_error: str | None,
    expected_calls: int,
) -> None:
    """Verify mountain country through Wikidata ISO country codes."""

    mountain_claims: dict[str, object] = {
        "P31": [entity_statement("Q8502")],
    }

    if isinstance(p17, list):
        mountain_claims["P17"] = [entity_statement(qid) for qid in p17]
    elif p17 is not None:
        mountain_claims["P17"] = p17

    country_entities = {
        qid: {
            "id": qid,
            "claims": {
                "P297": [
                    {
                        "rank": "normal",
                        "mainsnak": {
                            "snaktype": "value",
                            "datavalue": {
                                "type": "string",
                                "value": code,
                            },
                        },
                    },
                ],
            },
        }
        for qid, code in country_codes.items()
    }

    transport = Mock()
    transport.get_json.side_effect = [
        {
            "search": [
                {"id": "Q513", "label": "Mount Everest"},
            ],
        },
        {
            "entities": {
                "Q513": {
                    "id": "Q513",
                    "claims": mountain_claims,
                },
            },
        },
        {
            "entities": country_entities,
        },
    ]

    handler = WikidataMountainResolver(transport=transport)

    result = handler.execute(
        inputs={
            "name": "Mount Everest",
            "country": country,
        },
        parameters={"entity_type": "mountain"},
    )

    assert transport.get_json.call_count == expected_calls

    if expected_error is None:
        assert result.status == "success"
        assert result.value == EntityRef(
            source="wikidata",
            entity_id="Q513",
        )
        assert result.error_code is None
        assert result.evidence
        assert result.evidence[0].source == "wikidata"
        assert "P297" in result.evidence[0].reference
    else:
        assert result.status == "failure"
        assert result.error_code == expected_error
        assert result.value is None
        assert not result.evidence

    if expected_calls == 3:
        params = transport.get_json.call_args_list[2].kwargs["params"]
        assert params["action"] == "wbgetentities"
        assert params["ids"] == "|".join(country_codes)
        assert params["props"] == "claims"


@pytest.mark.parametrize(
    ("search_ids", "expected_qid", "expected_error", "expected_calls"),
    [
        (["Q9001", "Q9002"], "Q9002", None, 5),
        (["Q9003", "Q9002"], "Q9002", None, 5),
        (["Q9002", "Q9004"], None, "AMBIGUOUS", 5),
        (["Q9001", "Q9003"], None, "NOT_FOUND", 5),
        (["Q9002", "Q9002"], "Q9002", None, 3),
    ],
    ids=[
        "skip-non-mountain",
        "skip-wrong-country",
        "multiple-valid-mountains",
        "no-valid-mountains",
        "duplicate-search-result",
    ],
)
def test_resolves_multiple_mountain_candidates(
    search_ids: list[str],
    expected_qid: str | None,
    expected_error: str | None,
    expected_calls: int,
) -> None:
    """Resolve only unique, verified mountain candidates."""

    # Fictional candidate entities for offline testing.
    candidates = {
        "Q9001": ("Q5", "Q837"),
        "Q9002": ("Q8502", "Q837"),
        "Q9003": ("Q8502", "Q148"),
        "Q9004": ("Q8502", "Q837"),
    }

    entities: dict[str, object] = {
        qid: {
            "id": qid,
            "claims": {
                "P31": [entity_statement(entity_type)],
                "P17": [entity_statement(country_qid)],
            },
        }
        for qid, (entity_type, country_qid) in candidates.items()
    }

    # A non-mountain class without a path to Q8502.
    entities["Q5"] = {
        "id": "Q5",
        "claims": {},
    }

    for qid, code in (("Q837", "NP"), ("Q148", "CN")):
        entities[qid] = {
            "id": qid,
            "claims": {
                "P297": [
                    {
                        "rank": "normal",
                        "mainsnak": {
                            "snaktype": "value",
                            "datavalue": {
                                "type": "string",
                                "value": code,
                            },
                        },
                    },
                ],
            },
        }

    def respond(
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        assert url == WIKIDATA_API_URL
        assert timeout_seconds == 10.0

        if params["action"] == "wbsearchentities":
            return {
                "search": [{"id": qid, "label": "Mount Everest"} for qid in search_ids],
            }

        if params["action"] == "wbgetentities":
            return {
                "entities": {qid: entities[qid] for qid in params["ids"].split("|")},
            }

        raise AssertionError("Unexpected Wikidata action.")

    transport = Mock()
    transport.get_json.side_effect = respond

    handler = WikidataMountainResolver(transport=transport)

    result = handler.execute(
        inputs={
            "name": "Mount Everest",
            "country": "Nepal",
        },
        parameters={"entity_type": "mountain"},
    )

    assert transport.get_json.call_count == expected_calls

    if expected_error is None:
        assert expected_qid is not None
        assert result.status == "success"
        assert result.value == EntityRef(
            source="wikidata",
            entity_id=expected_qid,
        )
        assert result.evidence
    else:
        assert result.status == "failure"
        assert result.error_code == expected_error
        assert result.value is None
        assert not result.evidence


@pytest.mark.parametrize(
    ("name", "search_hits", "expected_qid", "expected_calls"),
    [
        (
            "Mount Everest",
            [{"id": "Q999", "label": "Annapurna"}],
            None,
            1,
        ),
        (
            "Everest",
            [
                {
                    "id": "Q513",
                    "label": "Mount Everest",
                    "match": {
                        "type": "alias",
                        "language": "en",
                        "text": "Everest",
                    },
                },
            ],
            "Q513",
            3,
        ),
        (
            "Mount Everest",
            [
                {"id": "Q999", "label": "Annapurna"},
                {"id": "Q513", "label": "Mount Everest"},
            ],
            "Q513",
            3,
        ),
        (
            "  mOuNt   EvErEsT  ",
            [{"id": "Q513", "label": "Mount Everest"}],
            "Q513",
            3,
        ),
    ],
    ids=[
        "unrelated-search-hit",
        "alias-match",
        "skip-unrelated-first-hit",
        "case-and-whitespace-normalization",
    ],
)
def test_resolver_checks_candidate_name(
    name: str,
    search_hits: list[dict[str, object]],
    expected_qid: str | None,
    expected_calls: int,
) -> None:
    """Verify name matching before requesting candidate details."""

    def respond(
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        assert url == WIKIDATA_API_URL
        assert timeout_seconds == 10.0

        if params["action"] == "wbsearchentities":
            return {"search": search_hits}

        if params["action"] == "wbgetentities":
            ids = params["ids"]

            if ids == "Q513":
                return {
                    "entities": {
                        "Q513": {
                            "id": "Q513",
                            "claims": {
                                "P31": [entity_statement("Q8502")],
                                "P17": [entity_statement("Q837")],
                            },
                        },
                    },
                }

            if ids == "Q837":
                return {
                    "entities": {
                        "Q837": {
                            "id": "Q837",
                            "claims": {
                                "P297": [
                                    {
                                        "rank": "normal",
                                        "mainsnak": {
                                            "snaktype": "value",
                                            "datavalue": {
                                                "type": "string",
                                                "value": "NP",
                                            },
                                        },
                                    },
                                ],
                            },
                        },
                    },
                }

        raise AssertionError("Unexpected Wikidata request.")

    transport = Mock()
    transport.get_json.side_effect = respond

    handler = WikidataMountainResolver(transport=transport)

    result = handler.execute(
        inputs={"name": name, "country": "Nepal"},
        parameters={"entity_type": "mountain"},
    )

    assert transport.get_json.call_count == expected_calls

    if expected_qid is None:
        assert result.status == "failure"
        assert result.error_code == "NOT_FOUND"
        assert result.value is None
        assert not result.evidence
    else:
        assert result.status == "success"
        assert result.value == EntityRef(
            source="wikidata",
            entity_id=expected_qid,
        )
        assert result.error_code is None
        assert result.evidence


@pytest.mark.parametrize(
    ("pages", "expected_qid", "expected_error", "expected_calls"),
    [
        (
            [
                {
                    "search": [{"id": "Q9001", "label": "Everest"}],
                    "search-continue": 50,
                },
                {"search": [{"id": "Q9002", "label": "Everest"}]},
            ],
            "Q9002",
            None,
            6,
        ),
        (
            [
                {
                    "search": [{"id": "Q9002", "label": "Everest"}],
                    "search-continue": 50,
                },
                {"search": [{"id": "Q9004", "label": "Everest"}]},
            ],
            None,
            "AMBIGUOUS",
            6,
        ),
        (
            [
                {
                    "search": [{"id": "Q9002", "label": "Everest"}],
                    "search-continue": 50,
                },
                {"search": [{"id": "Q9002", "label": "Everest"}]},
            ],
            "Q9002",
            None,
            4,
        ),
        (
            [
                {"search": [], "search-continue": 50},
                {"search": [{"id": "Q9002", "label": "Everest"}]},
            ],
            "Q9002",
            None,
            4,
        ),
        (
            [
                {
                    "search": [{"id": "Q9002", "label": "Everest"}],
                    "search-continue": "50",
                },
            ],
            None,
            "INVALID_RESPONSE",
            1,
        ),
        (
            [
                {"search": [], "search-continue": 50},
                {"search": [], "search-continue": 50},
            ],
            None,
            "INVALID_RESPONSE",
            2,
        ),
        (
            [
                {
                    "search": [{"id": "Q9002", "label": "Everest"}],
                    "search-continue": 50,
                },
                {"search": [], "search-continue": 100},
                {"search": [], "search-continue": 150},
            ],
            None,
            "INCOMPLETE_SEARCH",
            3,
        ),
    ],
    ids=[
        "match-on-second-page",
        "ambiguity-on-second-page",
        "duplicate-across-pages",
        "empty-first-page",
        "invalid-continuation-type",
        "repeated-continuation",
        "search-page-budget-exceeded",
    ],
)
def test_resolver_search_pagination(
    pages: list[dict[str, object]],
    expected_qid: str | None,
    expected_error: str | None,
    expected_calls: int,
) -> None:
    """Search all available pages before resolving a unique mountain."""

    search_requests: list[dict[str, str]] = []

    def respond(
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        assert url == WIKIDATA_API_URL
        assert timeout_seconds == 10.0

        if params["action"] == "wbsearchentities":
            page_index = len(search_requests)
            search_requests.append(dict(params))
            assert page_index < len(pages)
            return pages[page_index]

        if params["action"] == "wbgetentities":
            qid = params["ids"]

            if qid in {"Q9001", "Q9002", "Q9004"}:
                entity_type = "Q5" if qid == "Q9001" else "Q8502"
                return {
                    "entities": {
                        qid: {
                            "id": qid,
                            "claims": {
                                "P31": [entity_statement(entity_type)],
                                "P17": [entity_statement("Q837")],
                            },
                        },
                    },
                }

            if qid == "Q5":
                return {
                    "entities": {
                        "Q5": {
                            "id": "Q5",
                            "claims": {},
                        },
                    },
                }

            if qid == "Q837":
                return {
                    "entities": {
                        "Q837": {
                            "id": "Q837",
                            "claims": {
                                "P297": [
                                    {
                                        "rank": "normal",
                                        "mainsnak": {
                                            "snaktype": "value",
                                            "datavalue": {
                                                "type": "string",
                                                "value": "NP",
                                            },
                                        },
                                    },
                                ],
                            },
                        },
                    },
                }

        raise AssertionError("Unexpected Wikidata request.")

    transport = Mock()
    transport.get_json.side_effect = respond

    handler = WikidataMountainResolver(transport=transport)

    result = handler.execute(
        inputs={"name": "Everest", "country": "Nepal"},
        parameters={"entity_type": "mountain"},
    )

    assert transport.get_json.call_count == expected_calls
    assert len(search_requests) == len(pages)

    assert search_requests[0]["limit"] == "50"
    assert "continue" not in search_requests[0]

    if len(search_requests) > 1:
        assert search_requests[1]["continue"] == "50"

    if len(search_requests) > 2:
        assert search_requests[2]["continue"] == "100"

    if expected_error is None:
        assert expected_qid is not None
        assert result.status == "success"
        assert result.value == EntityRef(
            source="wikidata",
            entity_id=expected_qid,
        )
        assert result.evidence
    else:
        assert result.status == "failure"
        assert result.error_code == expected_error
        assert result.value is None
        assert not result.evidence


@pytest.mark.parametrize(
    ("property_id", "ranked_values", "expected_error", "expected_calls"),
    [
        (
            "P31",
            [("Q8502", "deprecated"), ("Q5", "normal")],
            "NOT_FOUND",
            3,
        ),
        (
            "P31",
            [("Q8502", "normal"), ("Q5", "preferred")],
            "NOT_FOUND",
            3,
        ),
        (
            "P31",
            [("Q8502", "preferred"), ("Q5", "normal")],
            None,
            3,
        ),
        (
            "P17",
            [("Q837", "normal"), ("Q148", "preferred")],
            "NOT_FOUND",
            3,
        ),
        (
            "P17",
            [("Q837", "preferred"), ("Q148", "normal")],
            None,
            3,
        ),
        (
            "P297",
            [("NP", "normal"), ("CN", "preferred")],
            "NOT_FOUND",
            3,
        ),
        (
            "P297",
            [("NP", "preferred"), ("CN", "normal")],
            None,
            3,
        ),
        (
            "P17",
            [("Q837", "deprecated")],
            "NOT_FOUND",
            2,
        ),
        (
            "P297",
            [("NP", "deprecated")],
            "NOT_FOUND",
            3,
        ),
    ],
    ids=[
        "deprecated-mountain-type",
        "preferred-non-mountain-type",
        "preferred-mountain-type",
        "preferred-different-country",
        "preferred-matching-country",
        "preferred-different-iso-code",
        "preferred-matching-iso-code",
        "deprecated-country-only",
        "deprecated-iso-code-only",
    ],
)
def test_resolver_uses_best_statement_ranks(
    property_id: str,
    ranked_values: list[tuple[str, str]],
    expected_error: str | None,
    expected_calls: int,
) -> None:
    """Apply Wikidata best-rank semantics to identity properties."""

    def make_statement(
        value: str,
        rank: str,
        *,
        is_string: bool = False,
    ) -> dict[str, object]:
        return {
            "rank": rank,
            "mainsnak": {
                "snaktype": "value",
                "datavalue": {
                    "type": "string" if is_string else "wikibase-entityid",
                    "value": value if is_string else {"id": value},
                },
            },
        }

    mountain_claims: dict[str, object] = {
        "P31": [make_statement("Q8502", "normal")],
        "P17": [make_statement("Q837", "normal")],
    }

    country_claims: dict[str, object] = {
        "P297": [make_statement("NP", "normal", is_string=True)],
    }

    if property_id == "P297":
        country_claims["P297"] = [
            make_statement(value, rank, is_string=True) for value, rank in ranked_values
        ]
    else:
        mountain_claims[property_id] = [
            make_statement(value, rank) for value, rank in ranked_values
        ]

    entities: dict[str, object] = {
        "Q513": {
            "id": "Q513",
            "claims": mountain_claims,
        },
        "Q837": {
            "id": "Q837",
            "claims": country_claims,
        },
        "Q148": {
            "id": "Q148",
            "claims": {
                "P297": [
                    make_statement("CN", "normal", is_string=True),
                ],
            },
        },
        "Q5": {
            "id": "Q5",
            "claims": {},
        },
    }

    def respond(
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        assert url == WIKIDATA_API_URL
        assert timeout_seconds == 10.0

        if params["action"] == "wbsearchentities":
            return {
                "search": [
                    {"id": "Q513", "label": "Mount Everest"},
                ],
            }

        if params["action"] == "wbgetentities":
            return {
                "entities": {qid: entities[qid] for qid in params["ids"].split("|")},
            }

        raise AssertionError("Unexpected Wikidata action.")

    transport = Mock()
    transport.get_json.side_effect = respond

    handler = WikidataMountainResolver(transport=transport)

    result = handler.execute(
        inputs={
            "name": "Mount Everest",
            "country": "Nepal",
        },
        parameters={"entity_type": "mountain"},
    )

    assert transport.get_json.call_count == expected_calls

    if expected_error is None:
        assert result.status == "success"
        assert result.value == EntityRef(
            source="wikidata",
            entity_id="Q513",
        )
        assert result.error_code is None
        assert result.evidence
    else:
        assert result.status == "failure"
        assert result.error_code == expected_error
        assert result.value is None
        assert not result.evidence


@pytest.mark.parametrize(
    ("class_graph", "expected_error", "expected_calls"),
    [
        ({"Q9100": ["Q8502"]}, None, 4),
        (
            {"Q9100": ["Q9101"], "Q9101": ["Q8502"]},
            None,
            5,
        ),
        ({"Q9100": []}, "NOT_FOUND", 3),
        (
            {"Q9100": ["Q9101"], "Q9101": ["Q9100"]},
            "NOT_FOUND",
            4,
        ),
        (
            {
                "Q9100": ["Q9101"],
                "Q9101": ["Q9102"],
                "Q9102": ["Q9103"],
            },
            "INCOMPLETE_HIERARCHY",
            5,
        ),
        ({"Q9100": "invalid"}, "INVALID_RESPONSE", 3),
        (
            {"Q9100": [f"Q{i}" for i in range(9200, 9225)]},
            "INCOMPLETE_HIERARCHY",
            3,
        ),
    ],
    ids=[
        "one-hop-subclass",
        "two-hop-subclass",
        "unrelated-class",
        "cyclic-hierarchy",
        "depth-limit",
        "malformed-p279",
        "node-budget",
    ],
)
def test_resolver_traverses_mountain_class_hierarchy(
    class_graph: dict[str, list[str] | str],
    expected_error: str | None,
    expected_calls: int,
) -> None:
    """Resolve mountain subclasses with bounded ontology traversal."""

    def respond(
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        assert url == WIKIDATA_API_URL
        assert timeout_seconds == 10.0

        if params["action"] == "wbsearchentities":
            return {
                "search": [{"id": "Q513", "label": "Mount Everest"}],
            }

        assert params["action"] == "wbgetentities"

        ids = params["ids"].split("|")

        if ids == ["Q513"]:
            return {
                "entities": {
                    "Q513": {
                        "id": "Q513",
                        "claims": {
                            "P31": [entity_statement("Q9100")],
                            "P17": [entity_statement("Q837")],
                        },
                    },
                },
            }

        if ids == ["Q837"]:
            return {
                "entities": {
                    "Q837": {
                        "id": "Q837",
                        "claims": {
                            "P297": [
                                {
                                    "rank": "normal",
                                    "mainsnak": {
                                        "snaktype": "value",
                                        "datavalue": {
                                            "type": "string",
                                            "value": "NP",
                                        },
                                    },
                                },
                            ],
                        },
                    },
                },
            }

        entities: dict[str, object] = {}

        for class_qid in ids:
            assert class_qid in class_graph
            parents = class_graph[class_qid]

            claims: dict[str, object] = {
                "P279": (
                    [entity_statement(qid) for qid in parents]
                    if isinstance(parents, list)
                    else parents
                ),
            }

            entities[class_qid] = {
                "id": class_qid,
                "claims": claims,
            }

        return {"entities": entities}

    transport = Mock()
    transport.get_json.side_effect = respond

    handler = WikidataMountainResolver(transport=transport)

    result = handler.execute(
        inputs={
            "name": "Mount Everest",
            "country": "Nepal",
        },
        parameters={"entity_type": "mountain"},
    )

    assert transport.get_json.call_count == expected_calls

    if expected_error is None:
        assert result.status == "success"
        assert result.value == EntityRef(
            source="wikidata",
            entity_id="Q513",
        )
        assert any("P279" in item.reference for item in result.evidence)
    else:
        assert result.status == "failure"
        assert result.error_code == expected_error
        assert result.value is None
        assert not result.evidence


@pytest.mark.parametrize(
    ("stage", "api_payload", "expected_error", "expected_calls"),
    [
        (
            "search",
            {"error": {"code": "badvalue", "info": "Invalid search"}},
            "PROVIDER_ERROR",
            1,
        ),
        (
            "candidate",
            {"error": {"code": "badvalue", "info": "Invalid entity"}},
            "PROVIDER_ERROR",
            2,
        ),
        (
            "country",
            {"error": {"code": "maxlag", "info": "Replica lag"}},
            "PROVIDER_ERROR",
            3,
        ),
        (
            "class",
            {"error": {"code": "maxlag", "info": "Replica lag"}},
            "PROVIDER_ERROR",
            3,
        ),
        (
            "elevation",
            {"error": {"code": "badvalue", "info": "Invalid property"}},
            "PROVIDER_ERROR",
            1,
        ),
        (
            "search",
            {"error": "malformed"},
            "INVALID_RESPONSE",
            1,
        ),
        (
            "search",
            {"success": 0},
            "INVALID_RESPONSE",
            1,
        ),
        (
            "candidate",
            {"entities": {"Q513": {"id": "Q513", "missing": ""}}},
            "NOT_FOUND",
            2,
        ),
        (
            "elevation",
            {"entities": {"Q513": {"id": "Q513", "missing": ""}}},
            "NOT_FOUND",
            1,
        ),
    ],
    ids=[
        "search-api-error",
        "candidate-api-error",
        "country-api-error",
        "class-api-error",
        "elevation-api-error",
        "malformed-error-envelope",
        "failed-success-flag",
        "missing-candidate",
        "missing-elevation-entity",
    ],
)
def test_wikidata_api_response_contracts(
    stage: str,
    api_payload: object,
    expected_error: str,
    expected_calls: int,
) -> None:
    """Distinguish API errors and missing entities from valid data."""

    def respond(
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        assert url == WIKIDATA_API_URL
        assert timeout_seconds == 10.0

        if params["action"] == "wbsearchentities":
            if stage == "search":
                return api_payload

            return {
                "search": [
                    {"id": "Q513", "label": "Mount Everest"},
                ],
            }

        assert params["action"] == "wbgetentities"

        ids = params["ids"]

        if ids == "Q513":
            if stage in {"candidate", "elevation"}:
                return api_payload

            entity_type = "Q9100" if stage == "class" else "Q8502"

            return {
                "entities": {
                    "Q513": {
                        "id": "Q513",
                        "claims": {
                            "P31": [entity_statement(entity_type)],
                            "P17": [entity_statement("Q837")],
                        },
                    },
                },
            }

        if ids == "Q9100" and stage == "class":
            return api_payload

        if ids == "Q837" and stage == "country":
            return api_payload

        raise AssertionError(f"Unexpected Wikidata request: {ids}")

    transport = Mock()
    transport.get_json.side_effect = respond

    if stage == "elevation":
        handler = WikidataElevationHandler(transport=transport)
        result = handler.execute(
            inputs={
                "entity": EntityRef(
                    source="wikidata",
                    entity_id="Q513",
                ),
            },
            parameters={"property": "elevation"},
        )
    else:
        handler = WikidataMountainResolver(transport=transport)
        result = handler.execute(
            inputs={
                "name": "Mount Everest",
                "country": "Nepal",
            },
            parameters={"entity_type": "mountain"},
        )

    assert transport.get_json.call_count == expected_calls
    assert result.status == "failure"
    assert result.error_code == expected_error
    assert result.value is None
    assert not result.evidence
