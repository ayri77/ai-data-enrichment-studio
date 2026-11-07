"""Preflight a user-approved second pass without running provider requests."""

from dataclasses import dataclass
from pathlib import Path

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
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


def load_resume_selections(
    *,
    source_path: Path,
    plan: EnrichmentPlan,
    approvals_path: Path,
    source_report_path: Path,
) -> tuple[dict[tuple[str, str], ResumeSelection], str]:
    """Fail closed when approval provenance or the plan's step contract changed."""
    manifest = ApprovalManifest.model_validate_json(approvals_path.read_text(encoding="utf-8-sig"))
    report = ResolutionJobReport.model_validate_json(
        source_report_path.read_text(encoding="utf-8-sig")
    )
    validated = validate_approval_manifest(manifest, report, source_path=source_path)
    operation_by_id = {operation.operation_id: operation for operation in report.operations}
    step_by_id = {step.id: step for step in plan.steps}

    selections: dict[tuple[str, str], ResumeSelection] = {}
    for key, approval in validated.items():
        step = step_by_id.get(approval.step_id)
        if (
            step is None
            or step.capability != "resolve_entity"
            or step.parameters != {"entity_type": "mountain"}
        ):
            raise ValueError(
                f"Approval targets an unsupported or changed plan step: {approval.step_id}."
            )
        source_report = operation_by_id[approval.operation_id].report
        selections[key] = ResumeSelection(approval=approval, source_report=source_report)

    return selections, report.job_id
