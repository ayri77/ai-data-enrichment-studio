"""Turn an audited job report into review items, and user decisions into approvals.

Nothing here contacts a provider. The review view is derived from the stored
resolution report; decisions are expanded into the existing approval manifest,
which is validated and freshly re-verified when the resume job runs.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.resolution.approvals import (
    REVIEWABLE_STATUSES,
    ApprovalManifest,
    ResolutionApproval,
    approval_blocker,
)
from ai_data_enrichment.resolution.geographic import GeographicRankingStrategy
from ai_data_enrichment.resolution.models import (
    ResolutionJobReport,
    ResolutionOperationAudit,
    ResolutionPolicy,
    ResolutionReport,
)

# Advisory only: ranks candidates for a suggestion and never selects one.
_RECOMMENDATION_POLICY = ResolutionPolicy(
    policy_id="review_recommendation",
    version="1.0",
    strategy="ranked",
    prefer_prominent=True,
)
_RECOMMENDATION_REASONS = (
    "country_capital_role",
    "administrative_seat_role",
    "population_recorded",
)


class ReviewDecision(BaseModel):
    """Choose one listed candidate for an operation, or leave it empty on purpose."""

    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=128)
    source: str | None = Field(default=None, min_length=1, max_length=64)
    entity_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def candidate_is_complete(self) -> "ReviewDecision":
        if (self.source is None) != (self.entity_id is None):
            raise ValueError("Give both source and entity_id, or neither to leave it empty.")
        return self


def _row_number(record_id: str) -> int | None:
    match = re.search(r"!(\d+)$", record_id)
    return int(match.group(1)) if match else None


def _recommendations(report: ResolutionReport) -> dict[tuple[str, str], list[str]]:
    """Return at most one suggested candidate with the reasons for suggesting it."""
    selectable = [
        candidate
        for candidate in report.candidate_set.candidates
        if approval_blocker(candidate, report) is None
    ]
    if len(selectable) == 1:
        reference = selectable[0].reference
        return {(reference.source, reference.entity_id): ["only_selectable_candidate"]}
    if report.candidate_set.provider_id != "open-meteo" or not selectable:
        return {}
    try:
        assessed = GeographicRankingStrategy().assess(report.candidate_set, _RECOMMENDATION_POLICY)
    except ValueError:
        return {}
    allowed = {(c.reference.source, c.reference.entity_id) for c in selectable}
    leaders = [
        item
        for item in assessed.candidates
        if item.rank == 1 and (item.reference.source, item.reference.entity_id) in allowed
    ]
    if len(leaders) != 1:
        return {}
    leader = leaders[0]
    reasons = [code for code in leader.reason_codes if code in _RECOMMENDATION_REASONS]
    return {(leader.reference.source, leader.reference.entity_id): reasons}


def _item(operation: ResolutionOperationAudit, capabilities: dict[str, str]) -> dict[str, Any]:
    report = operation.report
    assessments = {(a.reference.source, a.reference.entity_id): a for a in report.assessments}
    recommended = _recommendations(report)
    candidates = []
    for candidate in report.candidate_set.candidates:
        key = (candidate.reference.source, candidate.reference.entity_id)
        blocker = approval_blocker(candidate, report)
        assessment = assessments.get(key)
        candidates.append(
            {
                "source": key[0],
                "entity_id": key[1],
                "label": candidate.label,
                "entity_type": candidate.entity_type,
                "verification": candidate.verification,
                "attributes": {a.name: a.value for a in candidate.attributes},
                "evidence": [e.model_dump(mode="json") for e in candidate.evidence],
                "selectable": blocker is None,
                "blocker": blocker,
                "recommended": key in recommended,
                "recommendation_reasons": recommended.get(key, []),
                "reason_codes": list(assessment.reason_codes if assessment else ()),
            }
        )
    # Selectable first, the suggestion on top; otherwise keep the provider order.
    candidates.sort(key=lambda c: (not c["selectable"], not c["recommended"]))
    steps = sorted({link.step_id for link in operation.record_links})
    return {
        "operation_id": operation.operation_id,
        "capability": capabilities.get(steps[0]) if steps else None,
        "provider_id": report.candidate_set.provider_id,
        "query": {
            "name": report.candidate_set.query.name,
            "entity_type": report.candidate_set.query.entity_type,
            "context": {a.name: a.value for a in report.candidate_set.query.context},
        },
        "status": report.decision.status,
        "reason_codes": list(report.decision.reason_codes),
        "search_complete": report.candidate_set.search_complete,
        "affected": [
            {
                "record_id": link.record_id,
                "row": _row_number(link.record_id),
                "step_id": link.step_id,
            }
            for link in operation.record_links
        ],
        "candidates": candidates,
    }


def review_payload(job_id: str, report: ResolutionJobReport | None, plan: EnrichmentPlan) -> dict:
    """List operations a user can decide and those that are only informative."""
    capabilities = {step.id: step.capability for step in plan.steps}
    items: list[dict[str, Any]] = []
    informational: list[dict[str, Any]] = []
    for operation in report.operations if report else ():
        status = operation.report.decision.status
        if status in {"resolved", "auto_resolved"}:
            continue
        entry = _item(operation, capabilities)
        decidable = status in REVIEWABLE_STATUSES and any(
            candidate["selectable"] for candidate in entry["candidates"]
        )
        (items if decidable else informational).append(entry)
    return {
        "job_id": job_id,
        "previous_job_id": report.previous_job_id if report else None,
        "items": items,
        "informational": informational,
        "affected_rows": len({link["record_id"] for item in items for link in item["affected"]}),
    }


def approvals_from_decisions(
    decisions: list[ReviewDecision],
    report: ResolutionJobReport,
    *,
    workbook_sha256: str,
) -> ApprovalManifest | None:
    """Expand each operation-level choice to every record/step that shares it.

    Returns None when every decision leaves its operation empty. Provenance,
    candidate verification and eligibility are checked by the manifest
    validation that follows; this only rejects requests that cannot be expanded.
    """
    operations = {operation.operation_id: operation for operation in report.operations}
    seen: set[str] = set()
    approvals: list[ResolutionApproval] = []
    for decision in decisions:
        if decision.operation_id in seen:
            raise ValueError("An operation has more than one decision.")
        seen.add(decision.operation_id)
        operation = operations.get(decision.operation_id)
        if operation is None or operation.report.decision.status not in REVIEWABLE_STATUSES:
            raise ValueError("A decision targets an operation that does not need review.")
        if decision.source is None or decision.entity_id is None:
            continue
        approvals.extend(
            ResolutionApproval(
                record_id=link.record_id,
                step_id=link.step_id,
                operation_id=operation.operation_id,
                source=decision.source,
                entity_id=decision.entity_id,
            )
            for link in operation.record_links
        )
    if not approvals:
        return None
    return ApprovalManifest(
        source_job_id=report.job_id,
        workbook_sha256=workbook_sha256,
        approvals=tuple(approvals),
    )
