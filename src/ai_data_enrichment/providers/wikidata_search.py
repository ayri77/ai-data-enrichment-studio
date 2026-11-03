"""Bounded Wikidata search-page retrieval, without interpreting entity identities."""

from dataclasses import dataclass
from json import JSONDecodeError

from ai_data_enrichment.execution.models import OperationResult
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError
from ai_data_enrichment.providers.wikidata_constants import (
    WIKIDATA_API_URL,
    WIKIDATA_MAX_SEARCH_PAGES,
    WIKIDATA_SEARCH_PAGE_SIZE,
)
from ai_data_enrichment.providers.wikidata_parsing import _wikidata_api_failure


@dataclass(frozen=True)
class WikidataSearchBatch:
    """Candidate hits, whether all search pages were retrieved, and any failure."""

    items: tuple[object, ...]
    complete: bool
    failure: OperationResult | None = None


def fetch_wikidata_search(transport: JsonTransport, *, name: str) -> WikidataSearchBatch:
    """Fetch at most three pages, preserving observed candidates on truncation."""
    results: list[object] = []
    cursor: int | None = None
    for _ in range(WIKIDATA_MAX_SEARCH_PAGES):
        params = {
            "action": "wbsearchentities",
            "search": name.strip(),
            "language": "en",
            "format": "json",
            "type": "item",
            "limit": str(WIKIDATA_SEARCH_PAGE_SIZE),
        }
        if cursor is not None:
            params["continue"] = str(cursor)
        try:
            payload = transport.get_json(
                url=WIKIDATA_API_URL,
                params=params,
                timeout_seconds=10.0,
            )
        except (JsonTransportError, JSONDecodeError, OSError) as exc:
            return WikidataSearchBatch(
                items=tuple(results),
                complete=False,
                failure=transport_failure_result(exc, provider_label="Wikidata API"),
            )

        api_failure = _wikidata_api_failure(payload)
        if api_failure is not None:
            return WikidataSearchBatch(tuple(results), False, api_failure)
        if not isinstance(payload, dict):
            return _invalid_search(results, "Wikidata search returned an invalid response.")

        page = payload.get("search")
        if not isinstance(page, list) or len(page) > WIKIDATA_SEARCH_PAGE_SIZE:
            return _invalid_search(results, "Wikidata search results must be a bounded list.")
        results.extend(page)
        if "search-continue" not in payload:
            return WikidataSearchBatch(tuple(results), True)
        next_cursor = payload["search-continue"]
        if type(next_cursor) is not int or next_cursor <= (cursor or 0):
            return _invalid_search(results, "Invalid Wikidata search continuation.")
        cursor = next_cursor

    return WikidataSearchBatch(tuple(results), False)


def _invalid_search(items: list[object], message: str) -> WikidataSearchBatch:
    return WikidataSearchBatch(
        items=tuple(items),
        complete=False,
        failure=OperationResult(
            status="failure",
            error_code="INVALID_RESPONSE",
            message=message,
        ),
    )
