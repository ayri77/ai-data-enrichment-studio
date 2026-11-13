"""Facts shown next to a mountain candidate so that namesakes can be told apart."""

from typing import Any
from unittest.mock import Mock

from tests.support.wikidata import entity_statement

from ai_data_enrichment.providers.wikidata_facts import (
    MAX_DESCRIPTION_LENGTH,
    description_fact,
    recorded_facts,
)
from ai_data_enrichment.providers.wikidata_resolution import WikidataMountainResolutionService
from ai_data_enrichment.resolution.models import ResolutionPolicy

EARTH = "http://www.wikidata.org/entity/Q2"
MOON = "http://www.wikidata.org/entity/Q405"


def place(latitude: Any, longitude: Any, globe: str = EARTH, rank: str = "normal") -> dict:
    return {
        "rank": rank,
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {
                "type": "globecoordinate",
                "value": {"latitude": latitude, "longitude": longitude, "globe": globe},
            },
        },
    }


def height(amount: str, rank: str = "normal", unit: str = "Q11573") -> dict:
    return {
        "rank": rank,
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {
                "type": "quantity",
                "value": {"amount": amount, "unit": f"http://www.wikidata.org/entity/{unit}"},
            },
        },
    }


def facts(claims: dict) -> dict[str, object]:
    return {fact.name: fact.value for fact in recorded_facts("Q583", {"claims": claims})}


def test_the_search_description_is_kept_short_and_blank_ones_are_left_out() -> None:
    (fact,) = description_fact({"description": "  highest mountain\n in the Alps "})
    assert (fact.name, fact.value) == ("description", "highest mountain in the Alps")
    (long,) = description_fact({"description": "x" * 500})
    assert isinstance(long.value, str)
    assert len(long.value) == MAX_DESCRIPTION_LENGTH and long.value.endswith("…")
    assert description_fact({}) == ()
    assert description_fact({"description": "   "}) == ()
    assert description_fact({"description": 5}) == ()


def test_coordinates_and_the_preferred_elevation_are_read_from_the_fetched_claims() -> None:
    # Mont Blanc has six elevation statements, one of them preferred.
    claims = {
        "P625": [place(45.8327, 6.8651)],
        "P2044": [height("+4807.02"), height("+4805.59", rank="preferred"), height("+4810.02")],
    }
    assert facts(claims) == {"latitude": 45.8327, "longitude": 6.8651, "elevation_m": 4805.59}
    fact = recorded_facts("Q583", {"claims": claims})[-1]
    assert fact.evidence[0].reference == "Q583:P2044"


def test_a_fact_that_cannot_be_read_plainly_is_left_out_not_reported() -> None:
    # Several equally ranked values are not one fact.
    assert facts({"P2044": [height("+8848"), height("+8850")]}) == {}
    assert facts({"P625": [place(1.0, 2.0), place(3.0, 4.0)]}) == {}
    # A mountain on the Moon has coordinates, but not on a map of the Earth.
    assert facts({"P625": [place(26.0, 1.0, globe=MOON)]}) == {}
    # Not metres, out of range, malformed, deprecated, absent.
    assert facts({"P2044": [height("+29030", unit="Q3710")]}) == {}
    assert facts({"P625": [place(95.0, 2.0)]}) == {}
    assert facts({"P625": [place("45", "6")]}) == {}
    assert facts({"P625": [{"rank": "normal", "mainsnak": {"snaktype": "novalue"}}]}) == {}
    assert facts({"P2044": [height("+100", rank="deprecated")]}) == {}
    assert facts({"P2044": "broken", "P625": None}) == {}
    assert recorded_facts("Q1", None) == () and recorded_facts("Q1", {"claims": []}) == ()


def test_candidates_carry_the_facts_and_the_decision_does_not_depend_on_them() -> None:
    def entity(qid: str, extra: dict) -> dict:
        claims = {"P31": [entity_statement("Q8502")], "P17": [entity_statement("Q142")], **extra}
        return {"id": qid, "claims": claims}

    france = {"id": "Q142", "claims": {"P297": [_iso("FR")]}}
    entities = {
        "Q583": entity("Q583", {"P625": [place(45.83, 6.86)], "P2044": [height("+4805.59")]}),
        "Q31605638": entity("Q31605638", {"P625": [place(-49.4, 69.4)]}),
        "Q142": france,
    }
    hits = [
        {"id": "Q583", "label": "Mont Blanc", "description": "highest mountain in the Alps"},
        {"id": "Q31605638", "label": "Mont Blanc", "description": "mountain in the TAAF"},
    ]

    def respond(*, url: str, params: dict, timeout_seconds: float) -> object:
        if params["action"] == "wbsearchentities":
            return {"search": hits}
        return {"entities": {qid: entities[qid] for qid in params["ids"].split("|")}}

    transport = Mock()
    transport.get_json.side_effect = respond
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mont Blanc", "country": "France/Italy"},
        policy=ResolutionPolicy(policy_id="p", version="1", strategy="verified_only"),
    )
    shown = {
        c.reference.entity_id: {a.name: a.value for a in c.attributes}
        for c in outcome.report.candidate_set.candidates
    }
    assert shown["Q583"] == {
        "match_kind": "label",
        "description": "highest mountain in the Alps",
        "country_codes": "FR/IT",
        "latitude": 45.83,
        "longitude": 6.86,
        "elevation_m": 4805.59,
    }
    assert shown["Q31605638"]["description"] == "mountain in the TAAF"
    assert "elevation_m" not in shown["Q31605638"]
    # Two verified mountains in the requested countries: still a person's choice.
    assert outcome.report.decision.status == "needs_review"
    # Nothing was asked beyond the search, the candidates and their country.
    assert transport.get_json.call_count == 3


def _iso(code: str) -> dict[str, object]:
    return {
        "rank": "normal",
        "mainsnak": {"snaktype": "value", "datavalue": {"type": "string", "value": code}},
    }
