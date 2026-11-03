"""Offline controlled Wikidata mountain candidate resolution scenarios."""

from collections.abc import Mapping
from unittest.mock import Mock

import pytest
from tests.support.wikidata import entity_statement

from ai_data_enrichment.execution.models import EntityRef
from ai_data_enrichment.providers.wikidata import WikidataMountainResolver
from ai_data_enrichment.providers.wikidata_resolution import WikidataMountainResolutionService
from ai_data_enrichment.resolution.models import ResolutionPolicy, ResolutionReport


def _iso(code: str) -> dict[str, object]:
    return {
        "rank": "normal",
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {"type": "string", "value": code},
        },
    }


def _entity(
    qid: str,
    *,
    types: tuple[str, ...] = ("Q8502",),
    countries: tuple[str, ...] = ("Q837",),
) -> dict[str, object]:
    return {
        "id": qid,
        "claims": {
            "P31": [entity_statement(q) for q in types],
            "P17": [entity_statement(q) for q in countries],
        },
    }


def _search_hit(
    qid: str, label: str = "Mount Everest", *, alias: bool = False
) -> dict[str, object]:
    item: dict[str, object] = {"id": qid, "label": label}
    if alias:
        item["match"] = {"type": "alias", "text": "Mount Everest", "language": "en"}
    return item


def _transport(
    hits: list[dict[str, object]],
    *,
    entities: dict[str, object] | None = None,
    search_pages: list[dict[str, object]] | None = None,
    fail_on: str | None = None,
) -> Mock:
    provided: dict[str, object] = {
        "Q513": _entity("Q513", countries=("Q837", "Q148")),
        "Q61882061": _entity("Q61882061", types=("Q11424",), countries=()),
        "Q837": {"id": "Q837", "claims": {"P297": [_iso("NP")]}},
        "Q148": {"id": "Q148", "claims": {"P297": [_iso("CN")]}},
    }
    if entities:
        provided.update(entities)
    page_idx = 0

    def respond(
        *, url: str, params: Mapping[str, str], timeout_seconds: float
    ) -> object:
        nonlocal page_idx
        assert url == "https://www.wikidata.org/w/api.php"
        assert timeout_seconds == 10.0
        if params["action"] == "wbsearchentities":
            if search_pages is None:
                return {"search": hits}
            index = page_idx
            page_idx += 1
            return search_pages[index]

        assert params["action"] == "wbgetentities"
        ids = params["ids"].split("|")
        if fail_on is not None and fail_on in ids:
            return {"error": {"code": "maxlag", "info": "temporary"}}
        return {"entities": {qid: provided[qid] for qid in ids}}

    transport = Mock()
    transport.get_json.side_effect = respond
    return transport


def _policy(*, ranked: bool = False) -> ResolutionPolicy:
    return ResolutionPolicy(
        policy_id="mountain_review",
        version="1.0",
        strategy="ranked" if ranked else "strict",
        prefer_prominent=ranked,
        auto_accept_ranked=ranked,
    )


def test_everest_with_irrelevant_film_verifies_unique_mountain_without_film_bfs() -> None:
    transport = _transport(
        [
            _search_hit("Q61882061", "The Climbers", alias=True),
            _search_hit("Q513"),
        ]
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(),
    )
    assert outcome.operation.status == "success"
    assert outcome.operation.value == EntityRef(source="wikidata", entity_id="Q513")
    assert outcome.report.decision.status == "resolved"
    by_id = {c.reference.entity_id: c for c in outcome.report.candidate_set.candidates}
    assert by_id["Q513"].verification == "verified"
    assert by_id["Q61882061"].verification == "rejected"
    assert by_id["Q61882061"].reason_codes == ("direct_non_mountain_class",)
    assert "P31" in by_id["Q61882061"].evidence[0].reference
    assert outcome.report.candidate_set.search_complete
    assert outcome.report.candidate_set.verification_complete
    calls = transport.get_json.call_args_list
    assert len(calls) == 4  # Search + mountain claims + country + film claims
    assert all(call.kwargs["params"]["ids"] != "Q11424" for call in calls[1:])
    assert ResolutionReport.model_validate_json(outcome.report.model_dump_json()) == outcome.report


@pytest.mark.parametrize("reverse", [False, True])
def test_legacy_strict_resolver_also_skips_direct_film_class_without_hardcoded_q513(
    reverse: bool,
) -> None:
    hits = [_search_hit("Q513"), _search_hit("Q61882061", "The Climbers", alias=True)]
    transport = _transport(list(reversed(hits)) if reverse else hits)
    result = WikidataMountainResolver(transport).execute(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        parameters={"entity_type": "mountain"},
    )
    assert result.status == "success"
    assert result.value == EntityRef(source="wikidata", entity_id="Q513")
    assert len(transport.get_json.call_args_list) == 4


def test_mountain_country_can_match_either_of_multiple_verified_p17_countries() -> None:
    transport = _transport([_search_hit("Q513")])
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "China"}, policy=_policy()
    )
    assert outcome.report.decision.status == "resolved"
    assert outcome.operation.value == EntityRef(source="wikidata", entity_id="Q513")
    assert "P297" in outcome.operation.evidence[0].reference


def test_two_verified_mountains_are_ambiguous_even_under_ranked_policy() -> None:
    transport = _transport(
        [
            _search_hit("Q513"),
            _search_hit("Q9010", "Another Name", alias=True),
        ],
        entities={"Q9010": _entity("Q9010")},
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(ranked=True),
    )
    assert outcome.operation.error_code == "AMBIGUOUS"
    assert outcome.report.decision.status == "needs_review"
    assert outcome.report.decision.selected is None
    by_id = {a.reference.entity_id: a for a in outcome.report.assessments}
    assert by_id["Q513"].rank == 1
    assert by_id["Q9010"].rank == 2
    assert len(outcome.report.candidate_set.candidates) == 2


def test_unchecked_plausible_second_mountain_prevents_false_unique_selection() -> None:
    transport = _transport(
        [_search_hit("Q513"), _search_hit("Q9200")],
        entities={
            "Q9200": _entity("Q9200", types=("Q9100",)),
            "Q9100": {"id": "Q9100", "claims": {"P279": [entity_statement("Q9101")]}},
            "Q9101": {"id": "Q9101", "claims": {"P279": [entity_statement("Q9102")]}},
            "Q9102": {"id": "Q9102", "claims": {"P279": [entity_statement("Q9103")]}},
        },
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(ranked=True),
    )
    assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
    assert outcome.report.decision.selected is None
    assert outcome.report.decision.status == "incomplete_verification"
    by_id = {c.reference.entity_id: c for c in outcome.report.candidate_set.candidates}
    assert by_id["Q513"].verification == "verified"
    assert by_id["Q9200"].verification == "unchecked"
    assert outcome.report.candidate_set.incomplete_reason == "candidate_verification_incomplete"


def test_candidate_budget_prevents_uncertain_auto_acceptance_and_preserves_observations() -> None:
    transport = _transport(
        [_search_hit("Q513"), _search_hit("Q9201"), _search_hit("Q9202")],
        entities={
            "Q9201": _entity("Q9201"),
            "Q9202": _entity("Q9202"),
        },
    )
    outcome = WikidataMountainResolutionService(transport, max_verify_candidates=2).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(ranked=True),
    )
    assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
    assert (
        outcome.report.candidate_set.incomplete_reason
        == "candidate_verification_budget_exceeded"
    )
    assert len(outcome.report.candidate_set.candidates) == 3
    assert sum(c.verification == "unchecked" for c in outcome.report.candidate_set.candidates) == 1
    assert len(transport.get_json.call_args_list) <= 5


def test_search_page_limit_retains_unverified_hits_and_does_not_fetch_entities() -> None:
    pages = [
        {"search": [_search_hit("Q513")], "search-continue": 50},
        {"search": [_search_hit("Q61882061", "The Climbers", alias=True)], "search-continue": 100},
        {"search": [_search_hit("Q9200")], "search-continue": 150},
    ]
    transport = _transport([], search_pages=pages)
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(),
    )
    assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
    assert outcome.report.candidate_set.incomplete_reason == "search_pages_incomplete"
    assert [c.reference.entity_id for c in outcome.report.candidate_set.candidates] == [
        "Q513", "Q61882061", "Q9200"
    ]
    assert all(c.verification == "unchecked" for c in outcome.report.candidate_set.candidates)
    assert len(transport.get_json.call_args_list) == 3


def test_provider_error_after_verified_candidate_preserves_audited_evidence() -> None:
    transport = _transport(
        [_search_hit("Q513"), _search_hit("Q61882061", "The Climbers", alias=True)],
        fail_on="Q61882061",
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(),
    )
    assert outcome.operation.error_code == "PROVIDER_ERROR"
    assert outcome.report.decision.status == "provider_failure"
    assert outcome.report.decision.selected is None
    by_id = {c.reference.entity_id: c for c in outcome.report.candidate_set.candidates}
    assert by_id["Q513"].verification == "verified"
    assert by_id["Q61882061"].verification == "unchecked"


def test_missing_country_claims_are_unknown_not_proven_out_of_country() -> None:
    transport = _transport(
        [_search_hit("Q513"), _search_hit("Q9201")],
        entities={"Q9201": {"id": "Q9201", "claims": {"P31": [entity_statement("Q8502")]}}},
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"}, policy=_policy()
    )
    assert outcome.report.decision.status == "incomplete_verification"
    assert outcome.report.candidate_set.candidates[1].verification == "unchecked"


def test_empty_complete_search_is_unresolved_not_provider_failure() -> None:
    outcome = WikidataMountainResolutionService(_transport([])).resolve(
        inputs={"name": "Unknown Mountain", "country": "Nepal"}, policy=_policy()
    )
    assert outcome.report.decision.status == "unresolved"
    assert outcome.operation.error_code == "NOT_FOUND"
    assert outcome.report.candidate_set.search_complete


def test_search_api_drift_produces_provider_failure_with_no_fake_match() -> None:
    transport = Mock()
    transport.get_json.return_value = {"search": "not a list"}
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"}, policy=_policy()
    )
    assert outcome.report.decision.status == "provider_failure"
    assert outcome.operation.error_code == "INVALID_RESPONSE"


def test_invalid_exact_search_hit_yields_explicit_source_failure() -> None:
    transport = Mock()
    transport.get_json.return_value = {"search": [{"id": "not-QID", "label": "Mount Everest"}]}
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"}, policy=_policy()
    )
    assert outcome.report.decision.status == "provider_failure"
    assert outcome.operation.error_code == "INVALID_RESPONSE"


def test_mixed_p31_film_and_unknown_type_is_not_assumed_non_mountain() -> None:
    transport = _transport(
        [_search_hit("Q9210")],
        entities={
            "Q9210": _entity("Q9210", types=("Q11424", "Q9100")),
            "Q11424": {"id": "Q11424", "claims": {"P279": []}},
            "Q9100": {"id": "Q9100", "claims": {"P279": [entity_statement("Q9101")]}},
            "Q9101": {"id": "Q9101", "claims": {"P279": [entity_statement("Q9102")]}},
            "Q9102": {"id": "Q9102", "claims": {"P279": [entity_statement("Q9103")]}},
        },
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"}, policy=_policy()
    )
    assert outcome.report.decision.status == "incomplete_verification"
    assert outcome.report.candidate_set.candidates[0].verification == "unchecked"


def test_candidate_verification_budget_is_application_bounded() -> None:
    with pytest.raises(ValueError, match="outside application limits"):
        WikidataMountainResolutionService(Mock(), max_verify_candidates=0)
    with pytest.raises(ValueError, match="outside application limits"):
        WikidataMountainResolutionService(Mock(), max_verify_candidates=999)


def test_unknown_context_is_rejected_before_any_provider_call() -> None:
    transport = Mock()
    with pytest.raises(ValueError, match="Unregistered"):
        WikidataMountainResolutionService(transport).resolve(
            inputs={"name": "Mount Everest", "country": "Nepal", "unregistered": "ignored"},
            policy=_policy(),
        )
    transport.get_json.assert_not_called()
