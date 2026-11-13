"""The live diagnostic must show which unconfirmed candidates had a recorded type."""

from collections.abc import Mapping

from scripts.experiment_resolution import CountingTransport, inspect, summarize
from tests.support.wikidata import entity_statement

from ai_data_enrichment.evaluation.plan_run_fixtures import APITransportFixture
from ai_data_enrichment.providers.wikidata_constants import WIKIDATA_API_URL
from ai_data_enrichment.providers.wikidata_resolution import WikidataMountainResolutionService
from ai_data_enrichment.resolution.models import ResolutionPolicy


class NamesakeTransport(APITransportFixture):
    """One confirmed summit and one typed namesake without a country."""

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        if url == WIKIDATA_API_URL and params["action"] == "wbsearchentities":
            return {"search": [{"id": qid, "label": "Summit"} for qid in ("Q9001", "Q9002")]}
        if url == WIKIDATA_API_URL and params.get("props") == "labels":
            return {"entities": {"Q27686": {"labels": {"en": {"value": "hotel"}}}}}
        if url == WIKIDATA_API_URL and params["action"] == "wbgetentities":
            known = {
                "Q9001": {
                    "P31": [entity_statement("Q8502")],
                    "P17": [entity_statement("Q837")],
                },
                "Q9002": {"P31": [entity_statement("Q27686")]},
            }
            ids = params["ids"].split("|")
            if any(qid in known for qid in ids):
                return {"entities": {q: {"id": q, "claims": known.get(q, {})} for q in ids}}
        return super().get_json(url=url, params=params, timeout_seconds=timeout_seconds)


def test_report_separates_typed_namesakes_from_unknown_candidates() -> None:
    strict = ResolutionPolicy(policy_id="mountain_strict", version="1.0", strategy="strict")
    result = inspect(NamesakeTransport(), "Summit", "Nepal", strict)
    # Production behaviour today: the namesake blocks a strict resolution.
    assert result["status"] == "incomplete_verification" and result["selected"] is None
    by_id = {c["id"]: c for c in result["candidates"]}
    assert by_id["Q9001"]["verification"] == "verified"
    assert by_id["Q9002"] == {
        "id": "Q9002",
        "label": "Summit",
        "match": "label",
        "description": None,
        "elevation_m": None,
        "coordinates": None,
        "verification": "unchecked",
        "reason_codes": ["country_or_type_unverified"],
        "classes": ["Q27686 hotel"],
        "has_country": False,
    }
    # Called without a counting transport, nothing is claimed about requests.
    assert result["requests"] is None and result["seconds"] >= 0
    text = summarize(result)
    assert "unconfirmed: 1 | with a recorded type: 1 | without a country: 1" in text
    assert "type: Q27686 hotel | country: no" in text

    relaxed = ResolutionPolicy(
        policy_id="mountain_verified_only", version="1.0", strategy="verified_only"
    )
    accepted = inspect(NamesakeTransport(), "Summit", "Nepal", relaxed)
    assert accepted["status"] == "resolved" and accepted["selected"] == "Q9001"


def test_requests_are_counted_per_name_and_countries_are_remembered_between_names() -> None:
    policy = ResolutionPolicy(
        policy_id="mountain_verified_only", version="1.0", strategy="verified_only"
    )
    counting = CountingTransport(NamesakeTransport())
    service = WikidataMountainResolutionService(counting)
    first = inspect(counting, "Summit", "Nepal", policy, service=service)
    second = inspect(counting, "Summit", "Nepal", policy, service=service)
    # Search, the candidates' claims, and the country's code once for the whole run.
    assert (first["requests"], second["requests"]) == (3, 2)
    assert "| requests 3 |" in summarize(first)
