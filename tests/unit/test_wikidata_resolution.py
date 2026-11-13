"""Offline controlled Wikidata mountain candidate resolution scenarios."""

from collections.abc import Mapping
from unittest.mock import Mock

import pytest
from tests.support.wikidata import entity_statement

from ai_data_enrichment.execution.models import EntityRef
from ai_data_enrichment.providers.http_transport import JsonTransportError
from ai_data_enrichment.providers.wikidata import WikidataMountainResolver
from ai_data_enrichment.providers.wikidata_resolution import WikidataMountainResolutionService
from ai_data_enrichment.resolution.engine import ResolutionEngine
from ai_data_enrichment.resolution.models import CandidateSet, ResolutionPolicy, ResolutionReport
from ai_data_enrichment.resolution.mountain import MountainRankingStrategy


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


# A class that leads to "mountain" only through its parent. A candidate typed
# with it cannot be decided from its own claims: the walk costs a lookup.
PEAK = "Q9900"
PEAK_CLASS = {PEAK: {"id": PEAK, "claims": {"P279": [entity_statement("Q8502")]}}}


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
    entities: Mapping[str, object] | None = None,
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

    def respond(*, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
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


def _policy(*, ranked: bool = False, verified_only: bool = False) -> ResolutionPolicy:
    return ResolutionPolicy(
        policy_id="mountain_review",
        version="1.0",
        strategy="verified_only" if verified_only else "ranked" if ranked else "strict",
        prefer_prominent=ranked,
        auto_accept_ranked=ranked,
    )


@pytest.mark.parametrize("verified_only", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_single_positive_with_unknown_alternatives_obeys_policy(
    verified_only: bool, reverse: bool
) -> None:
    hits = [_search_hit("Q513"), _search_hit("Q9200"), _search_hit("Q9201")]
    transport = _transport(
        hits[::-1] if reverse else hits,
        entities={
            "Q9200": {"id": "Q9200", "claims": {}},
            "Q9201": {"id": "Q9201", "claims": {"P31": [entity_statement("Q9100")]}},
        },
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal/China"},
        policy=_policy(verified_only=verified_only),
    )
    assert outcome.report.candidate_set.candidate_scan_complete
    assert not outcome.report.candidate_set.verification_complete
    by_id = {c.reference.entity_id: c for c in outcome.report.candidate_set.candidates}
    assert by_id["Q513"].verification == "verified"
    assert by_id["Q9200"].verification == by_id["Q9201"].verification == "unchecked"
    if verified_only:
        assert outcome.operation.value == EntityRef(source="wikidata", entity_id="Q513")
        assert outcome.report.decision.basis == "verified_only"
        assert "uncertain" in outcome.report.decision.explanation
    else:
        assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
        assert outcome.report.decision.selected is None
    assert transport.get_json.call_count == 3  # Search, claims batch, country batch.
    assert ResolutionReport.model_validate_json(outcome.report.model_dump_json()) == outcome.report


def test_complete_type_walk_can_reject_an_unrelated_candidate_without_blacklists() -> None:
    transport = _transport(
        [_search_hit("Q513"), _search_hit("Q9200")],
        entities={
            "Q9200": _entity("Q9200", types=("Q9100",)),
            "Q9100": {"id": "Q9100", "claims": {"P279": []}},
        },
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"}, policy=_policy()
    )
    assert outcome.operation.status == "success"
    assert outcome.report.decision.basis == "unique_verified"
    alternative = outcome.report.candidate_set.candidates[1]
    assert alternative.verification == "rejected"
    assert alternative.reason_codes == ("no_mountain_class_path",)


@pytest.mark.parametrize("verified_only", [False, True])
def test_claim_batches_do_not_hide_an_unfinished_candidate_scan(verified_only: bool) -> None:
    entities = {f"Q{10000 + i}": _entity(f"Q{10000 + i}", types=(PEAK,)) for i in range(51)}
    hits = [_search_hit(qid) for qid in entities]
    transport = _transport(
        [],
        entities={**entities, **PEAK_CLASS},
        search_pages=[{"search": hits[:50], "search-continue": 50}, {"search": hits[50:]}],
    )
    outcome = WikidataMountainResolutionService(transport, max_verify_candidates=1).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=verified_only),
    )
    if verified_only:
        assert outcome.operation.value == EntityRef(source="wikidata", entity_id="Q10000")
        assert outcome.report.decision.basis == "verified_only"
        assert "unchecked_alternatives_remain" in outcome.report.decision.reason_codes
    else:
        assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
    assert not outcome.report.candidate_set.candidate_scan_complete
    assert (
        outcome.report.candidate_set.incomplete_reason == "candidate_verification_budget_exceeded"
    )
    assert len(outcome.report.candidate_set.candidates) == 51
    batches = [
        call.kwargs["params"]["ids"].split("|")
        for call in transport.get_json.call_args_list
        if call.kwargs["params"]["action"] == "wbgetentities"
    ]
    # Candidate claims in two batches, the one country, the class of the one
    # candidate the budget allowed.
    assert [len(batch) for batch in batches] == [50, 1, 1, 1]


def test_a_plausible_namesake_is_examined_even_when_many_unrelated_ones_come_first() -> None:
    # Fourteen claim-less namesakes sort before the two real candidates. With a
    # lookup budget of one, the old count-everything rule spent it on the first
    # unrelated namesake and accepted nothing; a verified-only run would then
    # have had no idea that a second real candidate existed.
    unrelated = {f"Q1{i:03}": {"id": f"Q1{i:03}", "claims": {}} for i in range(14)}
    real = {"Q513": _entity("Q513", types=(PEAK,)), "Q9010": _entity("Q9010", types=(PEAK,))}
    transport = _transport(
        [_search_hit(qid) for qid in (*unrelated, *real)],
        entities={**unrelated, **real, **PEAK_CLASS},
    )
    outcome = WikidataMountainResolutionService(transport, max_verify_candidates=1).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=True),
    )
    by_id = {c.reference.entity_id: c for c in outcome.report.candidate_set.candidates}
    assert all(by_id[q].reason_codes == ("country_or_type_unverified",) for q in unrelated)
    assert by_id["Q513"].verification == "verified"
    # The budget still bounds real lookups, and says so.
    assert by_id["Q9010"].reason_codes == ("search_match_only",)
    assert not outcome.report.candidate_set.candidate_scan_complete
    assert (
        outcome.report.candidate_set.incomplete_reason == "candidate_verification_budget_exceeded"
    )


def test_the_lookup_budget_follows_the_search_order_not_the_entity_ids() -> None:
    # The source returns the well-known summit first. Twelve streets with the
    # same name have IDs that sort before it as text, and each needs lookups.
    # Ordered by ID, a budget of two went to two streets and the summit was
    # never examined.
    streets = {
        f"Q1{i:02}": _entity(f"Q1{i:02}", types=("Q79007",), countries=("Q837",)) for i in range(12)
    }
    transport = _transport(
        [_search_hit("Q583"), *(_search_hit(qid) for qid in streets)],
        entities={
            "Q583": _entity("Q583"),
            **streets,
            "Q79007": {"id": "Q79007", "claims": {}},
        },
    )
    outcome = WikidataMountainResolutionService(transport, max_verify_candidates=2).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=True),
    )
    by_id = {c.reference.entity_id: c for c in outcome.report.candidate_set.candidates}
    assert by_id["Q583"].verification == "verified"
    assert outcome.operation.value == EntityRef(source="wikidata", entity_id="Q583")
    # The summit is typed as a mountain and its country is known, so it costs
    # nothing. The budget of two goes to two streets in the same country, which
    # are ruled out; ten are left unexamined.
    assert by_id["Q100"].reason_codes == ("no_mountain_class_path",)
    assert by_id["Q101"].reason_codes == ("no_mountain_class_path",)
    assert sum(c.reason_codes == ("search_match_only",) for c in by_id.values()) == 10
    assert not outcome.report.candidate_set.candidate_scan_complete


def _calls(transport: Mock) -> list[list[str]]:
    return [
        call.kwargs["params"]["ids"].split("|")
        for call in transport.get_json.call_args_list
        if call.kwargs["params"]["action"] == "wbgetentities"
    ]


def test_namesake_mountains_in_other_countries_cost_no_lookup_and_no_budget() -> None:
    # Twenty mountains with this name in the United States come first; the one in
    # Nepal is last. The budget is one. Before, each namesake cost a request for
    # its country's code and the budget ended at the first of them.
    abroad = {f"Q7{i:02}": _entity(f"Q7{i:02}", countries=("Q30",)) for i in range(20)}
    usa = {"Q30": {"id": "Q30", "claims": {"P297": [_iso("US")]}}}
    transport = _transport(
        [*(_search_hit(qid) for qid in abroad), _search_hit("Q513")],
        entities={**abroad, **usa},
    )
    service = WikidataMountainResolutionService(transport, max_verify_candidates=1)
    outcome = service.resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=True),
    )
    by_id = {c.reference.entity_id: c for c in outcome.report.candidate_set.candidates}
    assert outcome.operation.value == EntityRef(source="wikidata", entity_id="Q513")
    assert by_id["Q513"].verification == "verified"
    # Elsewhere is still "not confirmed for this country", as before, not "ruled out".
    assert all(by_id[q].reason_codes == ("country_or_type_unverified",) for q in abroad)
    # Every candidate was examined, with two requests after the search:
    # the candidates' claims, and the codes of the three countries they name.
    assert outcome.report.candidate_set.candidate_scan_complete
    assert [sorted(ids) for ids in _calls(transport)][1] == ["Q148", "Q30", "Q837"]
    assert len(_calls(transport)) == 2

    # The same countries in a later row of the same run are not asked about again.
    transport.get_json.reset_mock()
    service.resolve(
        inputs={"name": "Mount Everest", "country": "United States"},
        policy=_policy(verified_only=True),
    )
    assert len(_calls(transport)) == 1


def test_a_namesake_of_another_type_abroad_is_not_walked_up_the_class_tree() -> None:
    # The street's class is not in the scripted source at all: asking for it would fail.
    street = _entity("Q100", types=("Q79007",), countries=("Q30",))
    usa = {"Q30": {"id": "Q30", "claims": {"P297": [_iso("US")]}}}
    transport = _transport(
        [_search_hit("Q100"), _search_hit("Q513")], entities={"Q100": street, **usa}
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=True),
    )
    by_id = {c.reference.entity_id: c for c in outcome.report.candidate_set.candidates}
    assert by_id["Q513"].verification == "verified"
    assert by_id["Q100"].reason_codes == ("country_or_type_unverified",)
    assert not any("Q79007" in ids for ids in _calls(transport))


def test_a_failed_country_request_is_reported_by_the_candidate_that_needs_it() -> None:
    transport = _transport([_search_hit("Q513")], fail_on="Q837")
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"}, policy=_policy()
    )
    assert outcome.operation.status == "failure"
    assert outcome.report.decision.status == "provider_failure"
    assert outcome.report.decision.selected is None


def test_alias_matches_still_wait_behind_label_matches() -> None:
    transport = _transport(
        [_search_hit("Q9010", "Sagarmatha", alias=True), _search_hit("Q513")],
        entities={
            "Q513": _entity("Q513", types=(PEAK,)),
            "Q9010": _entity("Q9010", types=(PEAK,)),
            **PEAK_CLASS,
        },
    )
    outcome = WikidataMountainResolutionService(transport, max_verify_candidates=1).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=True),
    )
    by_id = {c.reference.entity_id: c for c in outcome.report.candidate_set.candidates}
    # The alias came first from the source, but the exact label is examined first.
    assert by_id["Q513"].verification == "verified"
    assert by_id["Q9010"].reason_codes == ("search_match_only",)


@pytest.mark.parametrize("verified_only", [False, True])
@pytest.mark.parametrize("positive_count", [1, 2])
def test_candidates_decided_from_fetched_claims_never_use_the_lookup_budget(
    verified_only: bool, positive_count: int
) -> None:
    positives = {"Q513": _entity("Q513")}
    if positive_count == 2:
        positives["Q9010"] = _entity("Q9010")
    unknowns = {
        f"Q{9200 + i}": {"id": f"Q{9200 + i}", "claims": {}} for i in range(15 - positive_count)
    }
    transport = _transport(
        [_search_hit(qid) for qid in (*positives, *unknowns)],
        entities={**positives, **unknowns},
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=verified_only),
    )
    candidate_set = outcome.report.candidate_set
    assert candidate_set.search_complete
    # Fifteen candidates exceed the 12-lookup budget in number, but the namesakes
    # without any claims are decided locally, so every candidate was examined.
    assert candidate_set.candidate_scan_complete
    assert not candidate_set.verification_complete
    assert candidate_set.provider_error_code is None
    assert candidate_set.incomplete_reason == "candidate_verification_incomplete"
    assert len(candidate_set.candidates) == 15
    assert sum(c.verification == "verified" for c in candidate_set.candidates) == positive_count
    assert (
        sum(c.verification == "unchecked" for c in candidate_set.candidates) == 15 - positive_count
    )
    # Unknown stays unknown: examined, not confirmed, and never silently dropped.
    assert not any(c.reason_codes == ("search_match_only",) for c in candidate_set.candidates)
    assert (
        sum(c.reason_codes == ("country_or_type_unverified",) for c in candidate_set.candidates)
        == 15 - positive_count
    )
    if verified_only and positive_count == 1:
        assert outcome.operation.value == EntityRef(source="wikidata", entity_id="Q513")
        assert outcome.report.decision.basis == "verified_only"
        assert "unchecked_alternatives_remain" in outcome.report.decision.reason_codes
        assert "Unchecked alternatives remain uncertain" in outcome.report.decision.explanation
        assert "P31" in outcome.operation.evidence[0].reference
        assert "P17" in outcome.operation.evidence[0].reference
    else:
        assert outcome.report.decision.selected is None
        assert outcome.operation.error_code == (
            "AMBIGUOUS" if verified_only else "INCOMPLETE_VERIFICATION"
        )
    assert ResolutionReport.model_validate_json(outcome.report.model_dump_json()) == outcome.report


@pytest.mark.parametrize("provider_error", [False, True])
def test_verified_only_never_selects_a_positive_after_search_or_provider_failure(
    provider_error: bool,
) -> None:
    positive = WikidataMountainResolutionService(_transport([_search_hit("Q513")])).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=True),
    )
    payload = positive.report.candidate_set.model_dump()
    payload.update(
        search_complete=provider_error,
        candidate_scan_complete=False,
        verification_complete=False,
        incomplete_reason="provider_verification_error"
        if provider_error
        else "search_pages_incomplete",
        provider_error_code="PROVIDER_ERROR" if provider_error else None,
    )
    report = ResolutionEngine().resolve(
        candidate_set=CandidateSet.model_validate(payload),
        policy=_policy(verified_only=True),
        strategy=MountainRankingStrategy(),
    )
    assert report.decision.selected is None
    assert report.decision.status == (
        "provider_failure" if provider_error else "incomplete_verification"
    )


@pytest.mark.parametrize("types", [(), ("Q9100",)])
def test_verified_only_requires_a_positive_candidate(types: tuple[str, ...]) -> None:
    transport = _transport(
        [_search_hit("Q9200")],
        entities={
            "Q9200": {"id": "Q9200", "claims": {"P31": [entity_statement(q) for q in types]}}
        },
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=True),
    )
    assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
    assert outcome.report.decision.selected is None


def test_verified_only_reports_ambiguity_even_with_an_unchecked_alternative() -> None:
    transport = _transport(
        [_search_hit("Q513"), _search_hit("Q9200"), _search_hit("Q9201")],
        entities={"Q9200": _entity("Q9200"), "Q9201": {"id": "Q9201", "claims": {}}},
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=True),
    )
    assert outcome.operation.error_code == "AMBIGUOUS"
    assert not outcome.report.candidate_set.verification_complete
    assert outcome.report.candidate_set.candidates[2].verification == "unchecked"


@pytest.mark.parametrize(
    ("class_entity", "error_code"),
    [
        ({"id": "Q9100"}, "INVALID_RESPONSE"),
        ({"id": "Q9100", "claims": {"P279": [{}]}}, "INVALID_RESPONSE"),
        (
            {
                "id": "Q9100",
                "claims": {"P279": [{"rank": "normal", "mainsnak": {"snaktype": "somevalue"}}]},
            },
            "INCOMPLETE_VERIFICATION",
        ),
    ],
)
def test_incomplete_type_walk_cannot_prove_rejection_or_a_positive_match(
    class_entity: object, error_code: str
) -> None:
    transport = _transport(
        [_search_hit("Q9200")],
        entities={"Q9200": _entity("Q9200", types=("Q9100",)), "Q9100": class_entity},
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=True),
    )
    assert outcome.operation.error_code == error_code
    assert outcome.report.decision.selected is None
    assert outcome.report.candidate_set.candidates[0].verification == "unchecked"


def test_mountain_country_can_match_either_of_multiple_verified_p17_countries() -> None:
    transport = _transport([_search_hit("Q513")])
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "China"}, policy=_policy()
    )
    assert outcome.report.decision.status == "resolved"
    assert outcome.operation.value == EntityRef(source="wikidata", entity_id="Q513")
    assert "P297" in outcome.operation.evidence[0].reference


@pytest.mark.parametrize("verified_only", [False, True])
def test_two_verified_mountains_are_ambiguous_even_under_ranked_policy(verified_only: bool) -> None:
    transport = _transport(
        [
            _search_hit("Q513"),
            _search_hit("Q9010", "Another Name", alias=True),
        ],
        entities={"Q9010": _entity("Q9010")},
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(ranked=not verified_only, verified_only=verified_only),
    )
    assert outcome.operation.error_code == "AMBIGUOUS"
    assert outcome.report.decision.status == "needs_review"
    assert outcome.report.decision.selected is None
    by_id = {a.reference.entity_id: a for a in outcome.report.assessments}
    assert by_id["Q513"].rank == 1
    assert by_id["Q9010"].rank == (1 if verified_only else 2)
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
            "Q513": _entity("Q513", types=(PEAK,)),
            "Q9201": _entity("Q9201", types=(PEAK,)),
            "Q9202": _entity("Q9202", types=(PEAK,)),
            **PEAK_CLASS,
        },
    )
    outcome = WikidataMountainResolutionService(transport, max_verify_candidates=2).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(ranked=True),
    )
    assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
    assert (
        outcome.report.candidate_set.incomplete_reason == "candidate_verification_budget_exceeded"
    )
    assert len(outcome.report.candidate_set.candidates) == 3
    assert sum(c.verification == "unchecked" for c in outcome.report.candidate_set.candidates) == 1
    assert len(transport.get_json.call_args_list) <= 5


@pytest.mark.parametrize("verified_only", [False, True])
def test_search_page_limit_retains_unverified_hits_and_does_not_fetch_entities(
    verified_only: bool,
) -> None:
    pages = [
        {"search": [_search_hit("Q513")], "search-continue": 50},
        {"search": [_search_hit("Q61882061", "The Climbers", alias=True)], "search-continue": 100},
        {"search": [_search_hit("Q9200")], "search-continue": 150},
    ]
    transport = _transport([], search_pages=pages)
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=verified_only),
    )
    assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
    assert outcome.report.candidate_set.incomplete_reason == "search_pages_incomplete"
    assert [c.reference.entity_id for c in outcome.report.candidate_set.candidates] == [
        "Q513",
        "Q61882061",
        "Q9200",
    ]
    assert all(c.verification == "unchecked" for c in outcome.report.candidate_set.candidates)
    assert len(transport.get_json.call_args_list) == 3


@pytest.mark.parametrize("verified_only", [False, True])
def test_provider_error_after_verified_candidate_preserves_audited_evidence(
    verified_only: bool,
) -> None:
    transport = _transport(
        [_search_hit("Q513"), _search_hit("Q9200", "Another Name", alias=True)],
        entities={"Q9200": _entity("Q9200", countries=("Q142",))},
        fail_on="Q142",
    )
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=verified_only),
    )
    assert outcome.operation.error_code == "PROVIDER_ERROR"
    assert outcome.report.decision.status == "provider_failure"
    assert outcome.report.decision.selected is None
    by_id = {c.reference.entity_id: c for c in outcome.report.candidate_set.candidates}
    assert by_id["Q513"].verification == "verified"
    assert by_id["Q9200"].verification == "unchecked"


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


def test_missing_country_skips_expensive_class_traversal() -> None:
    transport = _transport(
        [
            _search_hit("Q513"),
            _search_hit("Q84260471"),
        ],
        entities={
            "Q84260471": {
                "id": "Q84260471",
                "claims": {
                    "P31": [entity_statement("Q9100")],
                },
            },
        },
    )

    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(),
    )

    # The main mountain is verified, but the second candidate
    # remains uncertain because its country is unknown.
    assert outcome.report.decision.status == "incomplete_verification"

    candidates = {
        item.reference.entity_id: item for item in outcome.report.candidate_set.candidates
    }
    assert candidates["Q513"].verification == "verified"
    assert candidates["Q84260471"].verification == "unchecked"

    # No traversal into the unrelated class hierarchy.
    requested_ids = [
        call.kwargs["params"]["ids"]
        for call in transport.get_json.call_args_list
        if "ids" in call.kwargs["params"]
    ]
    assert "Q9100" not in requested_ids


@pytest.mark.parametrize(
    ("country", "source_country"),
    [("Nepal/China", "Q837"), ("China/Nepal", "Q837"), ("Pakistan/China", "Q148")],
)
@pytest.mark.parametrize("verified_only", [False, True])
def test_slash_separated_country_accepts_verified_membership(
    country: str,
    source_country: str,
    verified_only: bool,
) -> None:
    transport = _transport(
        [_search_hit("Q513")],
        entities={
            # The source verifies one of the requested countries.
            # One verified country from the input must suffice.
            "Q513": _entity(
                "Q513",
                countries=(source_country,),
            ),
        },
    )

    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={
            "name": "Mount Everest",
            "country": country,
        },
        policy=_policy(verified_only=verified_only),
    )

    assert outcome.operation.status == "success"
    assert outcome.operation.value == EntityRef(
        source="wikidata",
        entity_id="Q513",
    )
    assert outcome.report.decision.status == "resolved"
    assert "P297" in outcome.operation.evidence[0].reference


def test_legacy_resolver_accepts_slash_separated_countries() -> None:
    transport = _transport(
        [_search_hit("Q513")],
        entities={
            "Q513": _entity(
                "Q513",
                countries=("Q837",),
            ),
        },
    )

    result = WikidataMountainResolver(transport).execute(
        inputs={
            "name": "Mount Everest",
            "country": "Nepal/China",
        },
        parameters={"entity_type": "mountain"},
    )

    assert result.status == "success"
    assert result.value == EntityRef(
        source="wikidata",
        entity_id="Q513",
    )


@pytest.mark.parametrize("types", [None, (), ("Q9100",), ("Q11424", "Q9100")])
@pytest.mark.parametrize("budget", [1, 2])
def test_verification_keeps_missing_unknown_and_mixed_types_unchecked(
    types: tuple[str, ...] | None, budget: int
) -> None:
    transport = _transport(
        [_search_hit("Q513"), _search_hit("Q9200")],
        entities={
            "Q9200": {
                "id": "Q9200",
                "claims": {} if types is None else {"P31": [entity_statement(q) for q in types]},
            }
        },
    )
    outcome = WikidataMountainResolutionService(transport, max_verify_candidates=budget).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"}, policy=_policy()
    )
    assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
    assert outcome.report.decision.selected is None
    assert outcome.report.candidate_set.candidates[0].verification == "verified"
    alternative = outcome.report.candidate_set.candidates[1]
    assert alternative.verification == "unchecked"
    assert not alternative.evidence


@pytest.mark.parametrize(
    ("response", "error_code"),
    [
        (None, "INVALID_RESPONSE"),
        ({"entities": []}, "INVALID_RESPONSE"),
        ({"error": {"code": "maxlag"}}, "PROVIDER_ERROR"),
        (JsonTransportError("timeout"), "PROVIDER_ERROR"),
        (JsonTransportError("invalid_json"), "INVALID_RESPONSE"),
        (JsonTransportError("budget_exceeded"), "REQUEST_BUDGET_EXCEEDED"),
    ],
)
@pytest.mark.parametrize("verified_only", [False, True])
def test_batched_claims_failure_never_selects_a_candidate(
    response: object, error_code: str, verified_only: bool
) -> None:
    transport = Mock()
    transport.get_json.side_effect = [
        {"search": [_search_hit("Q513"), _search_hit("Q9200")]},
        response,
    ]
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=_policy(verified_only=verified_only),
    )
    assert outcome.operation.error_code == error_code
    assert outcome.report.decision.status == "provider_failure"
    assert outcome.report.decision.selected is None
    assert len(outcome.report.candidate_set.candidates) == 2
    assert all(c.verification == "unchecked" for c in outcome.report.candidate_set.candidates)


@pytest.mark.parametrize(
    ("entity", "error_code"),
    [
        (None, "INVALID_RESPONSE"),
        ({"id": "Q9200", "claims": []}, "INVALID_RESPONSE"),
        ({"id": "Q9200", "claims": {"P31": {}}}, "INVALID_RESPONSE"),
        (_entity("Q9201", types=("Q11424",)), "INCOMPLETE_VERIFICATION"),
        (
            {"id": "Q9200", "claims": {"P31": [entity_statement("Q11424"), {}]}},
            "INVALID_RESPONSE",
        ),
        (
            {
                "id": "Q9200",
                "claims": {"P31": [entity_statement("Q11424"), {"rank": "normal", "mainsnak": {}}]},
            },
            "INVALID_RESPONSE",
        ),
        *[
            (
                {
                    "id": "Q9200",
                    "claims": {
                        "P31": [
                            entity_statement("Q11424"),
                            {"rank": "normal", "mainsnak": {"snaktype": snaktype}},
                        ]
                    },
                },
                "INCOMPLETE_VERIFICATION",
            )
            for snaktype in ("somevalue", "novalue")
        ],
    ],
)
def test_verification_does_not_reject_incomplete_or_malformed_types(
    entity: object, error_code: str
) -> None:
    transport = _transport([_search_hit("Q513"), _search_hit("Q9200")], entities={"Q9200": entity})
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"}, policy=_policy()
    )
    assert outcome.operation.error_code == error_code
    assert outcome.report.decision.selected is None
    assert outcome.report.candidate_set.candidates[0].verification == "verified"
    alternative = outcome.report.candidate_set.candidates[1]
    assert alternative.verification == "unchecked"
    assert not alternative.evidence
