"""Offline provider contract regressions for response and semantic drift (#44)."""

from collections.abc import Mapping
from unittest.mock import Mock

import pytest
from tests.support.wikidata import entity_statement

from ai_data_enrichment.execution.models import EntityRef
from ai_data_enrichment.providers.open_meteo import OpenMeteoGeocodingHandler
from ai_data_enrichment.providers.open_meteo_resolution import OpenMeteoResolutionService
from ai_data_enrichment.providers.wikidata import WikidataElevationHandler
from ai_data_enrichment.providers.wikidata_resolution import WikidataMountainResolutionService
from ai_data_enrichment.providers.wikidata_verification import verify_wikidata_mountain_candidate
from ai_data_enrichment.resolution.models import ResolutionPolicy


def _mountain(*, qid: str = "Q513", mountain_class: str = "Q8502") -> dict[str, object]:
    return {
        "id": qid,
        "claims": {
            "P31": [entity_statement(mountain_class)],
            "P17": [entity_statement("Q837")],
        },
    }


def _country(*, qid: str = "Q837") -> dict[str, object]:
    return {
        "id": qid,
        "claims": {
            "P297": [
                {
                    "rank": "normal",
                    "mainsnak": {
                        "snaktype": "value",
                        "datavalue": {"type": "string", "value": "NP"},
                    },
                }
            ]
        },
    }


def _scripted_wikidata(entities: dict[str, object]) -> Mock:
    transport = Mock()

    def respond(*, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        assert url == "https://www.wikidata.org/w/api.php"
        assert timeout_seconds == 10.0
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": "Q513", "label": "Mount Everest"}]}
        assert params["action"] == "wbgetentities"
        requested = params["ids"].split("|")
        return {"entities": {qid: entities[qid] for qid in requested}}

    transport.get_json.side_effect = respond
    return transport


@pytest.mark.parametrize("reported_id", ["Q148", "Q999", 123])
def test_mountain_claims_for_other_entity_never_verify_requested_qid(
    reported_id: object,
) -> None:
    mountain = _mountain()
    mountain["id"] = reported_id
    transport = _scripted_wikidata({"Q513": mountain})
    result = verify_wikidata_mountain_candidate(transport, qid="Q513", expected_country_code="NP")
    assert result.status == "failure"
    assert result.error_code == (
        "INVALID_RESPONSE" if not isinstance(reported_id, str) else "INCOMPLETE_VERIFICATION"
    )
    assert transport.get_json.call_count == 1
    # A redirect to a different QID is not auto-followed or given forged Q513 Evidence.


def test_country_redirect_abstains_before_proving_mountain_country() -> None:
    transport = _scripted_wikidata({"Q513": _mountain(), "Q837": _country(qid="Q148")})
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=ResolutionPolicy(policy_id="strict", version="1"),
    )
    assert outcome.operation.status == "failure"
    assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
    assert outcome.report.decision.status == "incomplete_verification"
    assert outcome.report.decision.selected is None
    assert outcome.report.candidate_set.candidates[0].verification == "unchecked"
    assert transport.get_json.call_count == 3


def test_subclass_redirect_cannot_verify_mountain_type() -> None:
    mountain = _mountain(mountain_class="Q9900")
    class_abstract = {
        "id": "Q9901",  # Requested Q9900 is now a redirected or changed class.
        "claims": {"P279": [entity_statement("Q8502")]},
    }
    transport = _scripted_wikidata({"Q513": mountain, "Q9900": class_abstract})
    outcome = WikidataMountainResolutionService(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=ResolutionPolicy(policy_id="strict", version="1"),
    )
    assert outcome.operation.status == "failure"
    assert outcome.operation.error_code == "INCOMPLETE_VERIFICATION"
    assert outcome.report.decision.status == "incomplete_verification"
    assert outcome.report.decision.selected is None
    assert transport.get_json.call_count == 3


def test_elevation_response_redirect_never_attributes_height_to_old_qid() -> None:
    original = _mountain(qid="Q999")
    original["claims"] = {
        "P2044": [
            {
                "rank": "normal",
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
            }
        ],
    }
    transport = _scripted_wikidata({"Q513": original})
    result = WikidataElevationHandler(transport).execute(
        inputs={"entity": EntityRef(source="wikidata", entity_id="Q513")},
        parameters={"property": "elevation"},
    )
    assert result.status == "failure"
    assert result.value is None
    assert result.error_code == "INCOMPLETE_VERIFICATION"
    assert result.evidence == ()
    assert transport.get_json.call_count == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "true"},
        {"error": {"reason": "API changed"}},
        {"error": 1},
    ],
)
def test_malformed_open_meteo_error_envelope_is_not_treated_as_no_match(
    payload: dict[str, object],
) -> None:
    transport = Mock()
    transport.get_json.return_value = payload
    legacy = OpenMeteoGeocodingHandler(transport).execute(
        inputs={"city": "Berlin", "country": "Germany"},
        parameters={},
    )
    audited = OpenMeteoResolutionService(transport).resolve(
        inputs={"city": "Berlin", "country": "Germany"},
        policy=ResolutionPolicy(policy_id="strict", version="1"),
    )
    assert legacy.status == "failure"
    assert legacy.error_code == "INVALID_RESPONSE"
    assert audited.operation.error_code == "INVALID_RESPONSE"
    assert audited.report.decision.status == "provider_failure"
    assert audited.report.decision.selected is None


def test_wikidata_no_redirect_preserves_correct_entity_provenance() -> None:
    transport = _scripted_wikidata({"Q513": _mountain(), "Q837": _country()})
    result = verify_wikidata_mountain_candidate(transport, qid="Q513", expected_country_code="NP")
    assert result.status == "success"
    assert result.value == EntityRef(source="wikidata", entity_id="Q513")
    assert result.evidence
    assert all(e.source == "wikidata" for e in result.evidence)
