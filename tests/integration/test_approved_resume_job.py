"""Approved steps are row-scoped and never leak into ordinary resolution."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    PlanStep,
)
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionPolicy
from ai_data_enrichment.execution.models import EntityRef, Evidence, OperationResult, RuntimeValue
from ai_data_enrichment.resolution.models import (
    CandidateSet,
    ResolutionCandidate,
    ResolutionDecision,
    ResolutionPolicy,
    ResolutionQuery,
    ResolutionReport,
)
from ai_data_enrichment.resolution.sqlite_cache import SQLiteResolutionCache

REF = EntityRef(source="wikidata", entity_id="Q513")
EVIDENCE = (Evidence(source="wikidata", reference="Q513:P31,P17"),)
POLICY = ResolutionPolicy(policy_id="mountain_strict", version="1.0")


@dataclass(frozen=True)
class _Outcome:
    operation: OperationResult
    report: ResolutionReport


class _FailingHandler:
    def execute(
        self, *, inputs: Mapping[str, RuntimeValue], parameters: Mapping[str, str]
    ) -> OperationResult:
        raise AssertionError("Audited service must replace this handler")


class _Service:
    def __init__(self, *, approved: bool, resolved: bool = False) -> None:
        self.approved = approved
        self.resolved = resolved
        self.calls = 0

    def validate_inputs(self, inputs: Mapping[str, RuntimeValue]) -> None:
        assert inputs == {"name": "Mount Everest", "country": "Nepal"}

    def resolve(self, *, inputs: Mapping[str, RuntimeValue], policy: ResolutionPolicy) -> _Outcome:
        self.calls += 1
        assert policy == POLICY
        candidate = ResolutionCandidate(
            reference=REF,
            label="Mount Everest",
            entity_type="mountain",
            verification="verified",
            evidence=EVIDENCE,
        )
        report = ResolutionReport(
            candidate_set=CandidateSet(
                query=ResolutionQuery(name="Mount Everest", entity_type="mountain"),
                provider_id="wikidata",
                candidates=(candidate,),
                search_complete=self.resolved,
                verification_complete=self.resolved,
                incomplete_reason=None if self.resolved else "search_skipped_or_incomplete",
            ),
            policy=policy,
            decision=(
                ResolutionDecision(
                    status="resolved",
                    selected=REF,
                    basis="user_override" if self.approved else "unique_verified",
                    reason_codes=("user_approved",),
                    explanation="The approved entity was verified again.",
                )
                if self.approved or self.resolved
                else ResolutionDecision(
                    status="incomplete_verification",
                    reason_codes=("source_incomplete",),
                    explanation="Other candidates were not verified.",
                )
            ),
        )
        operation = (
            OperationResult(status="success", value=REF, evidence=EVIDENCE)
            if self.approved or self.resolved
            else OperationResult(
                status="failure", error_code="INCOMPLETE_VERIFICATION", message="Review required."
            )
        )
        return _Outcome(operation=operation, report=report)

    def validate_step(
        self,
        step: PlanStep,
        *,
        provider_id: str,
    ) -> None:
        if (
            step.capability != "resolve_entity"
            or step.parameters != {"entity_type": "mountain"}
            or provider_id != "wikidata"
        ):
            raise ValueError("Unsupported approved resolution step.")


def _plan() -> EnrichmentPlan:
    return EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": ColumnReference(kind="column", name="Mountain"),
                    "country": ColumnReference(kind="column", name="Country"),
                },
                parameters={"entity_type": "mountain"},
            )
        ],
        outputs=[],
        assumptions=[],
    )


def _records() -> list[InputRecord]:
    return [
        InputRecord(
            record_id=f"Mountains!{i}", values={"Mountain": "Mount Everest", "Country": "Nepal"}
        )
        for i in (2, 3)
    ]


def _job(
    normal: _Service,
    approved: _Service,
    *,
    record_id: str = "Mountains!2",
    resolution_cache: SQLiteResolutionCache | None = None,
) -> JobExecutor:
    return JobExecutor(
        handlers={"resolve_entity": _FailingHandler()},  # Audited service substitutes it.
        selected_providers={"resolve_entity": "wikidata"},
        configuration_ids={"resolve_entity": "wikidata-v1"},
        policy=JobExecutionPolicy(
            max_rows=2,
            batch_size=2,
            max_memoized_results=2,
            memoizable_providers=frozenset({("resolve_entity", "wikidata")}),
            persistent_cache_ttls=(
                {("resolve_entity", "wikidata"): 60.0} if resolution_cache is not None else {}
            ),
        ),
        capability_versions={"resolve_entity": "1.0"},
        provider_versions={"resolve_entity": "wikidata-v1"},
        resolution_services={"resolve_entity": normal},
        resolution_policies={"resolve_entity": POLICY},
        approved_resolution_services={(record_id, "resolve"): approved},
        resolution_cache=resolution_cache,
    )


def test_approved_step_does_not_leak_to_identical_unapproved_row() -> None:
    normal, approved = _Service(approved=False), _Service(approved=True)
    result = _job(normal, approved).execute(_plan(), _records(), previous_job_id="job-first")
    assert result.metrics.total_rows == 2
    assert result.metrics.successful_rows == 1
    assert result.metrics.failed_rows == 1
    assert approved.calls == 1
    assert normal.calls == 1
    assert result.metrics.executed_handler_calls == 2
    assert result.metrics.memoized_results == 0
    assert result.records[0].row_result.steps[0].result.status == "success"
    assert result.records[1].row_result.steps[0].result.status == "failure"
    assert result.resolution_report is not None
    assert result.resolution_report.previous_job_id == "job-first"
    assert len(result.resolution_report.operations) == 2
    decisions = {
        entry.record_links[0].record_id: entry.report.decision
        for entry in result.resolution_report.operations
    }
    assert decisions["Mountains!2"].basis == "user_override"
    assert decisions["Mountains!3"].status == "incomplete_verification"


def test_unknown_approval_record_fails_before_execution() -> None:
    normal, approved = _Service(approved=False), _Service(approved=True)
    with pytest.raises(ValueError, match="unknown record or step"):
        _job(normal, approved, record_id="Mountains!99").execute(_plan(), _records())
    assert normal.calls == 0
    assert approved.calls == 0


def test_approval_does_not_apply_to_another_step_on_the_same_row() -> None:
    normal, approved = _Service(approved=False), _Service(approved=True)
    plan = _plan()
    plan = plan.model_copy(
        update={"steps": [plan.steps[0], plan.steps[0].model_copy(update={"id": "other"})]}
    )
    result = _job(normal, approved).execute(plan, _records()[:1])
    assert approved.calls == normal.calls == 1
    assert [step.result.status for step in result.records[0].row_result.steps] == [
        "success",
        "failure",
    ]
    assert result.resolution_report is not None
    decisions = {
        link.step_id: item.report.decision
        for item in result.resolution_report.operations
        for link in item.record_links
    }
    assert decisions["resolve"].basis == "user_override"
    assert decisions["other"].status == "incomplete_verification"


def test_approved_results_are_fresh_and_never_written_to_persistent_cache(tmp_path: Path) -> None:
    normal, approved = _Service(approved=False), _Service(approved=True)
    with SQLiteResolutionCache(tmp_path / "resolution.sqlite") as cache:
        job = _job(normal, approved, resolution_cache=cache)
        first = job.execute(_plan(), _records()[:1])
        second = job.execute(_plan(), _records())
    assert approved.calls == 2
    assert normal.calls == 1
    assert first.metrics.persistent_cache_hits == first.metrics.persistent_cache_writes == 0
    assert first.metrics.persistent_cache_misses == 0
    assert second.metrics.persistent_cache_hits == second.metrics.persistent_cache_writes == 0
    assert second.metrics.persistent_cache_misses == 1
    assert second.metrics.successful_rows == second.metrics.failed_rows == 1


def test_normal_persistent_cache_hit_cannot_replace_approved_reverification(tmp_path: Path) -> None:
    normal, approved = _Service(approved=False, resolved=True), _Service(approved=True)
    with SQLiteResolutionCache(tmp_path / "resolution.sqlite") as cache:
        job = _job(normal, approved, record_id="Mountains!3", resolution_cache=cache)
        # Put an ordinary success for the same inputs into the paired cache.
        first = job.execute(_plan(), _records())
        second = job.execute(_plan(), _records())
    assert first.metrics.persistent_cache_writes == 1
    assert second.metrics.persistent_cache_hits == 1
    assert second.metrics.persistent_cache_writes == 0
    assert normal.calls == 1
    assert approved.calls == 2
    assert second.resolution_report is not None
    decisions = {
        link.record_id: item.report.decision
        for item in second.resolution_report.operations
        for link in item.record_links
    }
    assert decisions["Mountains!2"].basis == "unique_verified"
    assert decisions["Mountains!3"].basis == "user_override"


def test_invalid_approved_step_rejected_before_execution() -> None:
    normal = _Service(approved=False)
    approved = _Service(approved=True)

    plan = _plan().model_copy(deep=True)
    plan.steps[0].parameters["entity_type"] = "company"

    job = _job(normal, approved)

    with pytest.raises(
        ValueError,
        match="Unsupported approved resolution step",
    ):
        job.execute(plan, _records())

    assert normal.calls == 0
    assert approved.calls == 0
