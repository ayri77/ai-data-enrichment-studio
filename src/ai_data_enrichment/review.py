"""Turn an audited job report into review items, and user decisions into approvals.

Nothing here contacts a provider. The review view is derived from the stored
resolution report; decisions are expanded into the existing approval manifest,
which is validated and freshly re-verified when the resume job runs.
"""

import re
from collections.abc import Callable, Mapping
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
from ai_data_enrichment.resolution.input_errors import MAX_NAME_LENGTH
from ai_data_enrichment.resolution.models import (
    ResolutionJobReport,
    ResolutionOperationAudit,
    ResolutionPolicy,
    ResolutionReport,
)
from ai_data_enrichment.resolution.other_name import same_name
from ai_data_enrichment.resolution.resume import accepts_other_name

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
    """Choose a candidate, give another name to search for, or leave it empty."""

    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=128)
    source: str | None = Field(default=None, min_length=1, max_length=64)
    entity_id: str | None = Field(default=None, min_length=1, max_length=64)
    # Alone: search the source under this name when the job runs. With a
    # candidate: that candidate was found by a search under this name.
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_LENGTH)

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
            # The name the source was searched for, when the user gave another one.
            "searched_as": report.candidate_set.query.searched_as,
            "entity_type": report.candidate_set.query.entity_type,
            "context": {a.name: a.value for a in report.candidate_set.query.context},
        },
        "status": report.decision.status,
        "reason_codes": list(report.decision.reason_codes),
        "search_complete": report.candidate_set.search_complete,
        # Whether searching under another name could change this outcome.
        "other_name": accepts_other_name(report),
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
        if decision.name is not None:
            # Decisions that carry a name are validated on their own: see
            # names_from_decisions and searched_from_decisions.
            continue
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


def names_from_decisions(decisions: list[ReviewDecision]) -> dict[str, str]:
    """The operations the user gave another name for, with that name."""
    return {
        decision.operation_id: decision.name
        for decision in decisions
        if decision.name is not None and decision.entity_id is None
    }


def searched_from_decisions(decisions: list[ReviewDecision]) -> dict[str, tuple[str, str, str]]:
    """Candidates chosen from a search under another name: name, source, entity ID."""
    return {
        decision.operation_id: (decision.name, decision.source, decision.entity_id)
        for decision in decisions
        if decision.name is not None
        and decision.source is not None
        and decision.entity_id is not None
    }


def other_name_audit(
    decisions: list[ReviewDecision],
    report: ResolutionJobReport,
    proposals: Mapping[str, tuple[str, ...]],
    job_id: str,
) -> list[dict[str, Any]]:
    """Say for each other name of a resume who gave it: the model or the user.

    The origin is decided from the server's own record of what the model
    proposed for that operation, never from the request: ``model`` means the
    model had proposed exactly this name for this item before it was used.
    """
    operations = {operation.operation_id: operation for operation in report.operations}
    entries = []
    for decision in decisions:
        operation = operations.get(decision.operation_id)
        if decision.name is None or operation is None:
            continue
        searched_as = " ".join(decision.name.split())
        proposed = proposals.get(decision.operation_id, ())
        entries.append(
            {
                "job_id": job_id,
                "operation_id": decision.operation_id,
                "name": operation.report.candidate_set.query.name,
                "searched_as": searched_as,
                "origin": (
                    "model" if any(same_name(searched_as, name) for name in proposed) else "user"
                ),
            }
        )
    return entries


def searched_steps(
    resolution_report: dict[str, Any] | None, other_names: list[dict[str, Any]]
) -> dict[str, dict[str, dict[str, str]]]:
    """Map each record to the steps whose value was found under another name.

    Each step carries the name the source was asked for and, where the review
    recorded it, who gave that name.
    """
    origins = {(entry["name"], entry["searched_as"]): entry["origin"] for entry in other_names}
    steps: dict[str, dict[str, dict[str, str]]] = {}
    for operation in (resolution_report or {}).get("operations", ()):
        query = operation["report"]["candidate_set"]["query"]
        searched_as = query.get("searched_as")
        if searched_as is None or operation["report"]["decision"].get("selected") is None:
            continue
        found = {"searched_as": searched_as}
        origin = origins.get((query["name"], searched_as))
        if origin is not None:
            found["origin"] = origin
        for link in operation.get("record_links", ()):
            steps.setdefault(link["record_id"], {})[link["step_id"]] = found
    return steps


def searched_item(
    operation: ResolutionOperationAudit, found: ResolutionReport, plan: EnrichmentPlan
) -> dict[str, Any]:
    """A review item for what a search under another name found for an operation."""
    capabilities = {step.id: step.capability for step in plan.steps}
    return _item(operation.model_copy(update={"report": found}), capabilities)


def _steps_where(
    resolution_report: dict[str, Any] | None, accept: Callable[[dict[str, Any]], bool]
) -> dict[str, set[str]]:
    steps: dict[str, set[str]] = {}
    for operation in (resolution_report or {}).get("operations", ()):
        if accept(operation["report"]):
            for link in operation.get("record_links", ()):
                steps.setdefault(link["record_id"], set()).add(link["step_id"])
    return steps


def user_chosen_steps(resolution_report: dict[str, Any] | None) -> dict[str, set[str]]:
    """Map each record to the steps whose entity the user chose during review.

    Reads the serialized job report, so the public report can mark values that
    exist only because of a user decision. A value found under a name the user
    gave is one of them, whichever candidate the source then confirmed.
    """
    return _steps_where(
        resolution_report,
        lambda report: (
            report["decision"].get("basis") == "user_override"
            or (
                report["decision"].get("selected") is not None
                and report["candidate_set"]["query"].get("searched_as") is not None
            )
        ),
    )


def auto_accepted_steps(resolution_report: dict[str, Any] | None) -> dict[str, set[str]]:
    """Map each record to steps resolved without asking although doubt remained.

    That is the verified-only policy accepting the single confirmed candidate
    while other matches could not be confirmed. A clean unique match is not
    listed: nothing about it differs from a strict resolution.
    """
    return _steps_where(
        resolution_report,
        lambda report: (
            report["decision"].get("basis") == "verified_only"
            and "unchecked_alternatives_remain" in report["decision"].get("reason_codes", ())
        ),
    )
