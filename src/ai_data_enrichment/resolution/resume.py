"""Preflight a user-approved second pass without running provider requests."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.resolution.approvals import (
    ApprovalManifest,
    ResolutionApproval,
    validate_approval_manifest,
)
from ai_data_enrichment.resolution.input_errors import MAX_NAME_LENGTH
from ai_data_enrichment.resolution.models import ResolutionJobReport, ResolutionReport
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


def build_other_names(
    *,
    names: Mapping[str, str],
    report: ResolutionJobReport,
    plan: EnrichmentPlan,
) -> dict[tuple[str, str], OtherName]:
    """Expand each operation's new name to every record/step that shares it.

    Nothing is trusted from the request but the name itself: the operation must
    be one of the reviewed job, still undecided, and resolved by a lookup step
    that reads its inputs straight from the workbook.
    """
    operations = {operation.operation_id: operation for operation in report.operations}
    step_by_id = {step.id: step for step in plan.steps}
    selections: dict[tuple[str, str], OtherName] = {}
    for operation_id, name in names.items():
        operation = operations.get(operation_id)
        if operation is None or not accepts_other_name(operation.report):
            raise ValueError("Another name was given for an operation that cannot use one.")
        searched_as = " ".join(name.split())
        row_name = operation.report.candidate_set.query.name
        if not searched_as or len(searched_as) > MAX_NAME_LENGTH:
            raise ValueError("The name to search for is blank or too long.")
        if same_name(searched_as, row_name):
            raise ValueError("The name to search for is the row's own value.")
        for link in operation.record_links:
            step = step_by_id.get(link.step_id)
            if (
                step is None
                or not supports_other_name(step)
                or any(isinstance(ref, StepOutputReference) for ref in step.inputs.values())
            ):
                raise ValueError(f"Another name targets an unsupported plan step: {link.step_id}.")
            selections[(link.record_id, link.step_id)] = OtherName(
                operation_id=operation_id,
                capability=cast(OtherNameCapability, step.capability),
                row_name=row_name,
                searched_as=searched_as,
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
    if step.capability == "resolve_entity":
        return approval.source == "wikidata" and step.parameters == {"entity_type": "mountain"}
    if step.capability == "geocode":
        return approval.source == "open-meteo" and step.parameters in (
            {},
            {"required_role": "country_capital"},
        )
    return False


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
