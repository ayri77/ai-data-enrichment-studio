"""Preflight a user-approved second pass without running provider requests."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ai_data_enrichment.capabilities.bindings import find_binding
from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.resolution.approvals import (
    ApprovalManifest,
    ResolutionApproval,
    approval_blocker,
    validate_approval_manifest,
)
from ai_data_enrichment.resolution.input_errors import MAX_NAME_LENGTH
from ai_data_enrichment.resolution.models import (
    ResolutionJobReport,
    ResolutionOperationAudit,
    ResolutionReport,
)
from ai_data_enrichment.resolution.other_name import (
    OtherNameCapability,
    same_name,
    supports_other_name,
)

# Outcomes a different name can change. A provider failure or a country that
# was not recognized would fail the same way under any name.
OTHER_NAME_STATUSES = frozenset({"needs_review", "unresolved", "incomplete_verification"})


@dataclass(frozen=True, slots=True)
class ResumeSelection:
    """One workbook/record/step-scoped approval and its original source report."""

    approval: ResolutionApproval
    source_report: ResolutionReport


@dataclass(frozen=True, slots=True)
class OtherName:
    """Look one record/step up under a name the user gave instead of the cell's."""

    operation_id: str
    capability: OtherNameCapability
    row_name: str
    searched_as: str


def accepts_other_name(report: ResolutionReport) -> bool:
    """Whether searching under another name could change this outcome."""
    return (
        report.decision.status in OTHER_NAME_STATUSES
        and "country_not_recognized" not in report.decision.reason_codes
    )


def usable_other_name(name: str, row_name: str) -> str:
    """The name as it will be searched, or an error when it cannot help."""
    searched_as = " ".join(name.split())
    if not searched_as or len(searched_as) > MAX_NAME_LENGTH:
        raise ValueError("The name to search for is blank or too long.")
    if same_name(searched_as, row_name):
        raise ValueError("The name to search for is the row's own value.")
    return searched_as


def other_name_capability(
    operation: ResolutionOperationAudit, plan: EnrichmentPlan
) -> OtherNameCapability:
    """The lookup an operation's rows share, if another name can be given for it.

    The operation must still be undecided, and every step that used it must be
    an audited lookup that reads its inputs straight from the workbook.
    """
    if not accepts_other_name(operation.report):
        raise ValueError("Another name was given for an operation that cannot use one.")
    step_by_id = {step.id: step for step in plan.steps}
    capabilities: set[str] = set()
    for link in operation.record_links:
        step = step_by_id.get(link.step_id)
        if (
            step is None
            or not supports_other_name(step)
            or any(isinstance(ref, StepOutputReference) for ref in step.inputs.values())
        ):
            raise ValueError(f"Another name targets an unsupported plan step: {link.step_id}.")
        capabilities.add(step.capability)
    if len(capabilities) != 1:
        raise ValueError("Another name needs exactly one kind of lookup.")
    return cast(OtherNameCapability, capabilities.pop())


def build_other_names(
    *,
    names: Mapping[str, str],
    report: ResolutionJobReport,
    plan: EnrichmentPlan,
) -> dict[tuple[str, str], OtherName]:
    """Expand each operation's new name to every record/step that shares it.

    Nothing is trusted from the request but the name itself.
    """
    operations = {operation.operation_id: operation for operation in report.operations}
    selections: dict[tuple[str, str], OtherName] = {}
    for operation_id, name in names.items():
        operation = operations.get(operation_id)
        if operation is None:
            raise ValueError("Another name was given for an unknown operation.")
        capability = other_name_capability(operation, plan)
        row_name = operation.report.candidate_set.query.name
        searched_as = usable_other_name(name, row_name)
        for link in operation.record_links:
            selections[(link.record_id, link.step_id)] = OtherName(
                operation_id=operation_id,
                capability=capability,
                row_name=row_name,
                searched_as=searched_as,
            )
    return selections


def build_searched_selections(
    *,
    choices: Mapping[str, tuple[str, str, str]],
    searches: Mapping[str, ResolutionReport],
    report: ResolutionJobReport,
    plan: EnrichmentPlan,
) -> dict[tuple[str, str], ResumeSelection]:
    """Approve candidates the user chose from a search under another name.

    ``choices`` maps an operation to (name, source, entity ID); ``searches``
    holds the server's own report of the latest search for each operation. The
    choice must be a confirmed, not excluded candidate of exactly that search.
    The job verifies it again before using it.
    """
    operations = {operation.operation_id: operation for operation in report.operations}
    selections: dict[tuple[str, str], ResumeSelection] = {}
    for operation_id, (name, source, entity_id) in choices.items():
        operation = operations.get(operation_id)
        if operation is None:
            raise ValueError("A searched candidate was chosen for an unknown operation.")
        other_name_capability(operation, plan)
        searched_as = usable_other_name(name, operation.report.candidate_set.query.name)
        found = searches.get(operation_id)
        if found is None or found.candidate_set.query.searched_as != searched_as:
            raise ValueError("The chosen candidate does not come from the latest search.")
        matches = [
            candidate
            for candidate in found.candidate_set.candidates
            if (candidate.reference.source, candidate.reference.entity_id) == (source, entity_id)
        ]
        if len(matches) != 1 or approval_blocker(matches[0], found) is not None:
            raise ValueError("The chosen candidate was not confirmed by the search.")
        step_by_id = {step.id: step for step in plan.steps}
        for link in operation.record_links:
            approval = ResolutionApproval(
                record_id=link.record_id,
                step_id=link.step_id,
                operation_id=operation_id,
                source=source,
                entity_id=entity_id,
            )
            if not _resumable(step_by_id[link.step_id], approval):
                raise ValueError("The chosen candidate comes from another source.")
            selections[(link.record_id, link.step_id)] = ResumeSelection(
                approval=approval, source_report=found
            )
    return selections


def build_resume_selections(
    *,
    manifest: ApprovalManifest,
    report: ResolutionJobReport,
    plan: EnrichmentPlan,
    source_path: Path,
) -> dict[tuple[str, str], ResumeSelection]:
    """Fail closed when approval provenance or the plan's step contract changed."""
    validated = validate_approval_manifest(manifest, report, source_path=source_path)
    operation_by_id = {operation.operation_id: operation for operation in report.operations}
    step_by_id = {step.id: step for step in plan.steps}

    selections: dict[tuple[str, str], ResumeSelection] = {}
    for key, approval in validated.items():
        step = step_by_id.get(approval.step_id)
        if step is None or not _resumable(step, approval):
            raise ValueError(
                f"Approval targets an unsupported or changed plan step: {approval.step_id}."
            )
        source_report = operation_by_id[approval.operation_id].report
        selections[key] = ResumeSelection(approval=approval, source_report=source_report)

    return selections


def _resumable(step: PlanStep, approval: ResolutionApproval) -> bool:
    """An approval must use the source that resolves this exact step contract."""
    binding = find_binding(step.capability)
    if binding is None or binding.name_resolution is None:
        return False
    return (
        approval.source == binding.source_id
        and step.parameters in binding.name_resolution.approved_parameters
    )


def load_resume_selections(
    *,
    source_path: Path,
    plan: EnrichmentPlan,
    approvals_path: Path,
    source_report_path: Path,
) -> tuple[dict[tuple[str, str], ResumeSelection], str]:
    """Read a saved approval manifest and audit report, then validate them."""
    manifest = ApprovalManifest.model_validate_json(approvals_path.read_text(encoding="utf-8-sig"))
    report = ResolutionJobReport.model_validate_json(
        source_report_path.read_text(encoding="utf-8-sig")
    )
    selections = build_resume_selections(
        manifest=manifest, report=report, plan=plan, source_path=source_path
    )
    return selections, report.job_id
