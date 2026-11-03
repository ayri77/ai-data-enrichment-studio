"""Offline contract tests for reusable resolution reports and review manifests."""

from math import nan

import pytest
from pydantic import ValidationError

from ai_data_enrichment.execution.models import EntityRef, Evidence
from ai_data_enrichment.resolution.models import (
    CandidateSet,
    ResolutionAttribute,
    ResolutionCandidate,
    ResolutionDecision,
    ResolutionJobReport,
    ResolutionOperationAudit,
    ResolutionPolicy,
    ResolutionQuery,
    ResolutionRecordLink,
    ResolutionReport,
)


def _candidate(
    entity_id: str,
    *,
    verification: str = "verified",
    entity_type: str = "city",
) -> ResolutionCandidate:
    return ResolutionCandidate(
        reference=EntityRef(source="open-meteo", entity_id=entity_id),
        label="Berlin",
        entity_type=entity_type,
        verification=verification,
        attributes=(
            ResolutionAttribute(name="country_code", value="DE"),
            ResolutionAttribute(name="administrative_area", value="Berlin"),
        ),
        evidence=(Evidence(source="open-meteo", reference=f"geonames:{entity_id}"),)
        if verification == "verified"
        else (),
    )


def _set(
    *,
    candidates: tuple[ResolutionCandidate, ...] | None = None,
    search_complete: bool = True,
    verification_complete: bool = True,
    incomplete_reason: str | None = None,
    provider_error_code: str | None = None,
) -> CandidateSet:
    return CandidateSet(
        provider_id="open-meteo",
        query=ResolutionQuery(
            name="Berlin",
            entity_type="city",
            context=(
                ResolutionAttribute(name="country_code", value="DE"),
            ),
        ),
        candidates=candidates if candidates is not None else (_candidate("2950159"),),
        search_complete=search_complete,
        verification_complete=verification_complete,
        incomplete_reason=incomplete_reason,
        provider_error_code=provider_error_code,
    )


def _policy(*, ranked: bool = False) -> ResolutionPolicy:
    if ranked:
        return ResolutionPolicy(
            policy_id="geo_prominence",
            version="1.0",
            strategy="ranked",
            prefer_prominent=True,
            auto_accept_ranked=True,
        )
    return ResolutionPolicy(policy_id="strict", version="1.0")


def _decision(
    status: str = "resolved",
    selected: EntityRef | None = None,
    basis: str | None = "unique_verified",
) -> ResolutionDecision:
    return ResolutionDecision(
        status=status,
        selected=selected if selected is not None else _candidate("2950159").reference,
        basis=basis,
        reason_codes=("exact_match",),
        explanation="Matched by country and entity identity.",
    )


def _report(*, ranked: bool = False) -> ResolutionReport:
    return ResolutionReport(
        candidate_set=_set(),
        policy=_policy(ranked=ranked),
        decision=_decision(
            status="auto_resolved" if ranked else "resolved",
            basis="ranking_policy" if ranked else "unique_verified",
        ),
    )


def test_resolution_report_round_trips_strict_json() -> None:
    report = _report()
    assert report.schema_version == "1.0"
    assert report.decision.status == "resolved"
    payload = report.model_dump(mode="json")
    assert payload["candidate_set"]["candidates"][0]["reference"]["entity_id"] == "2950159"
    assert payload["decision"]["reason_codes"] == ["exact_match"]
    assert ResolutionReport.model_validate_json(report.model_dump_json()) == report


def test_auto_resolved_berlin_preserves_alternatives_without_probabilities() -> None:
    berlin_small = _candidate("2950158")
    report = ResolutionReport(
        candidate_set=_set(candidates=(_candidate("2950159"), berlin_small)),
        policy=_policy(ranked=True),
        decision=_decision(status="auto_resolved", basis="ranking_policy"),
    )
    assert report.decision.selected == _candidate("2950159").reference
    assert len(report.candidate_set.candidates) == 2
    assert "confidence" not in report.model_dump_json()


def test_administrative_context_is_typed_and_does_not_change_source_values() -> None:
    request = ResolutionQuery(
        name="Berlin",
        entity_type="city",
        context=(
            ResolutionAttribute(name="country_code", value="DE"),
            ResolutionAttribute(name="administrative_area", value="Schleswig-Holstein"),
        ),
    )
    assert request.context[1].value == "Schleswig-Holstein"
    assert request.name == "Berlin"
    assert request.model_dump(mode="json")["context"][1]["name"] == "administrative_area"


def test_incomplete_everest_records_unverified_candidates_without_selection() -> None:
    query = ResolutionQuery(
        name="Mount Everest",
        entity_type="mountain",
        context=(ResolutionAttribute(name="country_code", value="NP"),),
    )
    candidates = CandidateSet(
        query=query,
        provider_id="wikidata",
        candidates=(
            ResolutionCandidate(
                reference=EntityRef(source="wikidata", entity_id="Q513"),
                label="Mount Everest",
                entity_type="mountain",
                verification="verified",
                evidence=(Evidence(source="wikidata", reference="Q513:P31,P17"),),
            ),
            ResolutionCandidate(
                reference=EntityRef(source="wikidata", entity_id="Q21939846"),
                label="Mount Everest",
                entity_type="mountain",
                verification="unchecked",
            ),
        ),
        search_complete=True,
        verification_complete=False,
        incomplete_reason="hierarchy_budget_exceeded",
    )
    report = ResolutionReport(
        candidate_set=candidates,
        policy=_policy(),
        decision=ResolutionDecision(
            status="incomplete_verification",
            reason_codes=("unchecked_candidate",),
            explanation="Further candidate verification exceeded the configured budget.",
        ),
    )
    assert report.decision.selected is None
    assert report.candidate_set.candidates[0].verification == "verified"
    assert report.candidate_set.candidates[1].verification == "unchecked"


def test_no_match_requires_complete_search_and_is_not_provider_error() -> None:
    report = ResolutionReport(
        candidate_set=_set(candidates=()),
        policy=_policy(),
        decision=ResolutionDecision(
            status="unresolved",
            reason_codes=("no_exact_match",),
            explanation="No location matched the original spelling.",
        ),
    )
    assert report.candidate_set.candidates == ()
    assert report.decision.status == "unresolved"


def test_provider_failure_preserves_transport_error_code() -> None:
    report = ResolutionReport(
        candidate_set=_set(
            candidates=(),
            search_complete=False,
            verification_complete=False,
            provider_error_code="HTTP_429",
        ),
        policy=_policy(),
        decision=ResolutionDecision(
            status="provider_failure",
            reason_codes=("transport_failure",),
            explanation="Provider request failed.",
        ),
    )
    assert report.candidate_set.provider_error_code == "HTTP_429"


def test_review_report_and_cancelled_job_are_independent_of_execution_status() -> None:
    report = ResolutionReport(
        candidate_set=_set(candidates=(_candidate("2950159"), _candidate("2950158"))),
        policy=_policy(),
        decision=ResolutionDecision(
            status="needs_review",
            reason_codes=("ambiguous_exact_match",),
            explanation="Two matching cities need clarification.",
        ),
    )
    operation = ResolutionOperationAudit(
        operation_id="op-geo-berlin-de",
        report=report,
        record_links=(
            ResolutionRecordLink(record_id="row-1", step_id="a"),
            ResolutionRecordLink(record_id="row-20", step_id="b"),
        ),
    )
    job = ResolutionJobReport(
        job_id="job-1",
        status="cancelled",
        total_rows=100,
        processed_rows=20,
        operations=(operation,),
    )
    assert len(job.operations) == 1
    assert len(job.operations[0].record_links) == 2
    assert job.status == "cancelled"
    assert job.total_rows - job.processed_rows == 80
    assert ResolutionJobReport.model_validate_json(job.model_dump_json()) == job


def test_second_pass_can_link_previous_job_and_user_override() -> None:
    candidate_set = _set(
        candidates=(_candidate("2950159"), _candidate("2950158")),
        search_complete=False,
        verification_complete=False,
        incomplete_reason="page_budget_exceeded",
    )
    report = ResolutionReport(
        candidate_set=candidate_set,
        policy=_policy(),
        decision=ResolutionDecision(
            status="resolved",
            selected=EntityRef(source="open-meteo", entity_id="2950158"),
            basis="user_override",
            reason_codes=("user_selected_entity",),
            explanation="User explicitly confirmed the Schleswig-Holstein entity.",
        ),
    )
    job = ResolutionJobReport(
        job_id="job-2",
        previous_job_id="job-1",
        status="completed",
        total_rows=1,
        processed_rows=1,
        operations=(
            ResolutionOperationAudit(
                operation_id="op-geo-berlin-de-sh",
                revision=2,
                report=report,
                record_links=(ResolutionRecordLink(record_id="row-1", step_id="a"),),
            ),
        ),
    )
    assert job.previous_job_id == "job-1"
    assert job.operations[0].revision == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"name": " ", "entity_type": "city"},
        {
            "name": "Berlin",
            "entity_type": "city",
            "context": [
                {"name": "country_code", "value": "DE"},
                {"name": "country_code", "value": "DE"},
            ],
        },
        {"name": "Berlin", "entity_type": "city", "unexpected": True},
    ],
)
def test_invalid_resolution_queries_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ResolutionQuery.model_validate(payload)


@pytest.mark.parametrize("value", [" ", nan, float("inf"), {"any": "json"}])
def test_attributes_reject_blank_non_finite_and_unstructured_values(value: object) -> None:
    with pytest.raises(ValidationError):
        ResolutionAttribute.model_validate({"name": "population", "value": value})


@pytest.mark.parametrize(
    ("candidates", "search_complete", "verification_complete", "reason", "error"),
    [
        ((_candidate("2950159"), _candidate("2950159")), True, True, None, None),
        ((_candidate("2950159"),), False, True, None, None),
        ((_candidate("2950159"),), True, True, "truncated", None),
        ((_candidate("2950159"),), True, True, None, "HTTP_500"),
    ],
)
def test_candidate_set_rejects_conflicting_or_duplicate_metadata(
    candidates: tuple[ResolutionCandidate, ...],
    search_complete: bool,
    verification_complete: bool,
    reason: str | None,
    error: str | None,
) -> None:
    with pytest.raises(ValidationError):
        _set(
            candidates=candidates,
            search_complete=search_complete,
            verification_complete=verification_complete,
            incomplete_reason=reason,
            provider_error_code=error,
        )


def test_verified_candidate_must_have_evidence() -> None:
    with pytest.raises(ValidationError):
        ResolutionCandidate(
            reference=EntityRef(source="wikidata", entity_id="Q513"),
            label="Mount Everest",
            entity_type="mountain",
            verification="verified",
        )


def test_strict_policy_rejects_prominence_auto_acceptance() -> None:
    with pytest.raises(ValidationError):
        ResolutionPolicy(
            policy_id="strict",
            version="1.0",
            strategy="strict",
            prefer_prominent=True,
        )


def test_ranked_auto_acceptance_requires_explicit_opt_in() -> None:
    with pytest.raises(ValidationError):
        ResolutionReport(
            candidate_set=_set(),
            policy=ResolutionPolicy(policy_id="ranked", version="1.0", strategy="ranked"),
            decision=_decision(status="auto_resolved", basis="ranking_policy"),
        )


def test_incomplete_candidate_set_cannot_be_auto_accepted() -> None:
    with pytest.raises(ValidationError):
        ResolutionReport(
            candidate_set=_set(
                search_complete=True,
                verification_complete=False,
                incomplete_reason="hierarchy_budget_exceeded",
            ),
            policy=_policy(ranked=True),
            decision=_decision(status="auto_resolved", basis="ranking_policy"),
        )


def test_unverified_entity_cannot_be_selected_by_user_override() -> None:
    with pytest.raises(ValidationError):
        ResolutionReport(
            candidate_set=_set(candidates=(_candidate("2950159", verification="unchecked"),)),
            policy=_policy(),
            decision=_decision(status="resolved", basis="user_override"),
        )


def test_non_selected_decisions_must_not_contain_final_entity() -> None:
    with pytest.raises(ValidationError):
        _decision(status="needs_review", basis="unique_verified")


def test_incomplete_search_cannot_be_reported_as_not_found() -> None:
    with pytest.raises(ValidationError):
        ResolutionReport(
            candidate_set=_set(
                candidates=(),
                search_complete=False,
                verification_complete=False,
                incomplete_reason="page_budget_exceeded",
            ),
            policy=_policy(),
            decision=ResolutionDecision(
                status="unresolved",
                reason_codes=("no_match",),
                explanation="No match found.",
            ),
        )


def test_operation_links_and_job_counts_are_consistent() -> None:
    valid = ResolutionOperationAudit(
        operation_id="op-1",
        report=_report(),
        record_links=(ResolutionRecordLink(record_id="row-1", step_id="a"),),
    )
    with pytest.raises(ValidationError):
        ResolutionOperationAudit(
            operation_id="op-1",
            report=_report(),
            record_links=(valid.record_links[0], valid.record_links[0]),
        )
    with pytest.raises(ValidationError):
        ResolutionJobReport(
            job_id="job-1",
            status="completed",
            total_rows=1,
            processed_rows=2,
            operations=(valid,),
        )
    with pytest.raises(ValidationError):
        ResolutionJobReport(
            job_id="job-1",
            status="completed",
            total_rows=1,
            processed_rows=1,
            operations=(valid, valid),
        )
