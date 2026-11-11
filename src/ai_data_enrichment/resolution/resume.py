"""Preflight a user-approved second pass without running provider requests."""

from dataclasses import dataclass
from pathlib import Path

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan, PlanStep
from ai_data_enrichment.resolution.approvals import (
    ApprovalManifest,
    ResolutionApproval,
    validate_approval_manifest,
)
from ai_data_enrichment.resolution.models import ResolutionJobReport, ResolutionReport


@dataclass(frozen=True, slots=True)
class ResumeSelection:
    """One workbook/record/step-scoped approval and its original source report."""

    approval: ResolutionApproval
    source_report: ResolutionReport


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
