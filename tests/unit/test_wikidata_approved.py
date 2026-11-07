"""Approved mountain lookup checks one QID, not all textual matches."""

from collections.abc import Mapping

import pytest
from tests.support.wikidata import entity_statement

from ai_data_enrichment.execution.models import EntityRef, Evidence
from ai_data_enrichment.providers.wikidata_approved import ApprovedWikidataMountainResolutionService
from ai_data_enrichment.resolution.approvals import ResolutionApproval
from ai_data_enrichment.resolution.models import (
    CandidateSet,
    ResolutionAttribute,
    ResolutionCandidate,
    ResolutionDecision,
    ResolutionPolicy,
    ResolutionQuery,
    ResolutionReport,
)


class ApprovedTransport:
    def __init__(self, *, mountain: bool = True) -> None:
        self.requests: list[str] = []
        self.mountain = mountain

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        assert url == "https://www.wikidata.org/w/api.php"
        assert timeout_seconds == 10.0
        assert params["action"] == "wbgetentities"  # No wbsearchentities.
        qids = params["ids"]
        self.requests.append(qids)
        if qids == "Q513":
            return {
                "entities": {
                    "Q513": {
                        "id": "Q513",
                        "claims": {
                            "P31": [entity_statement("Q8502" if self.mountain else "Q11424")],
                            "P17": [entity_statement("Q837")],
                        },
                    }
                }
            }
        if qids == "Q837":
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
                                        "datavalue": {"type": "string", "value": "NP"},
                                    },
                                }
                            ]
                        },
                    }
                }
            }
        raise AssertionError(f"Unexpected requested QID: {qids}")


def _approval() -> ResolutionApproval:
    return ResolutionApproval(
        record_id="Mountains!2",
        step_id="resolve",
        operation_id="operation-abc",
        source="wikidata",
        entity_id="Q513",
    )


def _report() -> ResolutionReport:
    return ResolutionReport(
        policy=ResolutionPolicy(policy_id="mountain_strict", version="1.0"),
        candidate_set=CandidateSet(
            query=ResolutionQuery(
                name="Mount Everest",
                entity_type="mountain",
                context=(ResolutionAttribute(name="country_code", value="NP"),),
            ),
            provider_id="wikidata",
            candidates=(
                ResolutionCandidate(
                    reference=EntityRef(source="wikidata", entity_id="Q513"),
                    label="Mount Everest",
                    entity_type="mountain",
                    verification="verified",
                    evidence=(Evidence(source="wikidata", reference="Q513:P31,P17"),),
                ),
            ),
            search_complete=True,
            verification_complete=False,
            incomplete_reason="candidate_verification_incomplete",
        ),
        decision=ResolutionDecision(
            status="incomplete_verification",
            reason_codes=("source_incomplete",),
            explanation="Other candidates were not verified.",
        ),
    )


def _service(transport: ApprovedTransport) -> ApprovedWikidataMountainResolutionService:
    return ApprovedWikidataMountainResolutionService(
        transport,
        approval=_approval(),
        source_report=_report(),
    )


def test_approved_qid_is_freshly_verified_without_search() -> None:
    transport = ApprovedTransport()
    outcome = _service(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=ResolutionPolicy(policy_id="mountain_strict", version="1.0"),
    )
    assert outcome.operation.status == "success"
    assert outcome.operation.value == EntityRef(source="wikidata", entity_id="Q513")
    assert outcome.report.decision.basis == "user_override"
    assert outcome.report.decision.status == "resolved"
    assert outcome.report.candidate_set.search_complete is False
    assert transport.requests == ["Q513", "Q837"]


def test_source_type_drift_invalidates_user_approval() -> None:
    transport = ApprovedTransport(mountain=False)
    outcome = _service(transport).resolve(
        inputs={"name": "Mount Everest", "country": "Nepal"},
        policy=ResolutionPolicy(policy_id="mountain_strict", version="1.0"),
    )
    assert outcome.operation.status == "failure"
    assert outcome.report.decision.selected is None
    assert transport.requests == ["Q513"]


def test_mismatched_input_fails_before_any_source_request() -> None:
    transport = ApprovedTransport()
    service = _service(transport)
    with pytest.raises(ValueError, match="country differs"):
        service.resolve(
            inputs={"name": "Mount Everest", "country": "China"},
            policy=ResolutionPolicy(policy_id="mountain_strict", version="1.0"),
        )
    assert transport.requests == []
