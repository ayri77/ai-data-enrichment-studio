"""Sanitized source diagnostics for deliberately enabled live smoke runs.

Only expected, public provider fields are exposed. Never log full URLs,
headers, arbitrary responses, or raw user-supplied workbook contents.
"""

from collections.abc import Mapping

from ai_data_enrichment.providers.http_transport import JsonTransport


def _entity_references(statements: object, *, limit: int = 16) -> list[dict[str, str]]:
    """Summarize a bounded set of Wikidata entity-reference statements."""
    if not isinstance(statements, list):
        return []
    values: list[dict[str, str]] = []
    for statement in statements[:limit]:
        if not isinstance(statement, dict):
            continue
        snak = statement.get("mainsnak")
        if not isinstance(snak, dict):
            continue
        data = snak.get("datavalue")
        if not isinstance(data, dict) or data.get("type") != "wikibase-entityid":
            continue
        value = data.get("value")
        qid = value.get("id") if isinstance(value, dict) else None
        if isinstance(qid, str) and qid.startswith("Q") and qid[1:].isdigit():
            values.append({"id": qid, "rank": str(statement.get("rank", "unknown"))})
    return values


def _summarize_geocode(response: object) -> dict[str, object]:
    if not isinstance(response, dict):
        return {"response_type": type(response).__name__}
    results = response.get("results")
    if not isinstance(results, list):
        return {"result_count": 0, "provider_error": response.get("error") is True}
    items = []
    for item in results[:10]:
        if isinstance(item, dict):
            items.append(
                {
                    key: item[key]
                    for key in (
                        "id",
                        "name",
                        "country_code",
                        "feature_code",
                        "admin1",
                        "admin2",
                        "population",
                    )
                    if key in item and isinstance(item[key], (str, int))
                }
            )
    return {"result_count": len(results), "candidates": items}


def _summarize_wikidata(action: str, response: object) -> dict[str, object]:
    if not isinstance(response, dict):
        return {"response_type": type(response).__name__}
    if response.get("error") is not None:
        error = response["error"]
        return {"provider_error_code": error.get("code") if isinstance(error, dict) else "invalid"}
    if action == "wbsearchentities":
        results = response.get("search")
        if not isinstance(results, list):
            return {"response_type": "invalid_search"}
        items = []
        for item in results[:30]:
            if not isinstance(item, dict):
                continue
            match = item.get("match")
            items.append(
                {
                    "id": item.get("id"),
                    "label": item.get("label"),
                    "match": match.get("text") if isinstance(match, dict) else None,
                }
            )
        return {
            "result_count": len(results),
            "has_continuation": "search-continue" in response,
            "candidates": items,
        }
    if action == "wbgetentities":
        entities = response.get("entities")
        if not isinstance(entities, dict):
            return {"response_type": "invalid_entities"}
        items = []
        for qid, entity in list(entities.items())[:32]:
            claims = entity.get("claims") if isinstance(entity, dict) else None
            if not isinstance(claims, dict):
                items.append({"id": qid, "claims": "missing_or_invalid"})
                continue
            items.append(
                {
                    "id": qid,
                    "P31": _entity_references(claims.get("P31")),
                    "P279": _entity_references(claims.get("P279")),
                    "P17": _entity_references(claims.get("P17")),
                    "has_P2044": "P2044" in claims,
                }
            )
        return {"entities": items}
    return {"action": "unknown"}


class SourceTraceTransport:
    """Decorate a JSON transport without changing its inputs or responses."""

    def __init__(self, transport: JsonTransport) -> None:
        self._transport = transport
        self.events: list[dict[str, object]] = []

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        response = self._transport.get_json(url=url, params=params, timeout_seconds=timeout_seconds)
        if url == "https://geocoding-api.open-meteo.com/v1/search":
            self.events.append(
                {
                    "provider": "open-meteo",
                    "query": params.get("name"),
                    **_summarize_geocode(response),
                }
            )
        elif url == "https://www.wikidata.org/w/api.php":
            action = params.get("action", "")
            self.events.append(
                {
                    "provider": "wikidata",
                    "action": action,
                    "search": params.get("search") if action == "wbsearchentities" else None,
                    "ids": params.get("ids") if action == "wbgetentities" else None,
                    **_summarize_wikidata(action, response),
                }
            )
        return response
