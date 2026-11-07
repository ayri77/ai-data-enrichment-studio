"""Validate user-approved entity selections against an audited source job.

Approvals are inert data: the execution layer must explicitly re-verify the
approved entity against the current row's hard constraints before using it.
"""

import re
from hashlib import sha256
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_data_enrichment.resolution.models import ResolutionJobReport


class ResolutionApproval(BaseModel):
    """Approve one verified source candidate for one record and plan step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)

    @field_validator("record_id", "step_id", "operation_id", "source", "entity_id")
    @classmethod
    def no_blank_identifiers(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Approval identifiers cannot be blank.")
        return value

    @model_validator(mode="after")
    def source_specific_id(self) -> Self:
        if self.source == "wikidata" and re.fullmatch(r"Q[1-9]\d*", self.entity_id) is None:
            raise ValueError("Wikidata approvals require a valid QID.")
        return self


class ApprovalManifest(BaseModel):
    """Bind approved choices to one source workbook and one audited job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    source_job_id: str = Field(min_length=1)
    workbook_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approvals: tuple[ResolutionApproval, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_record_steps(self) -> Self:
        keys = [(item.record_id, item.step_id) for item in self.approvals]
        if len(keys) != len(set(keys)):
            raise ValueError("A record and step cannot have multiple approvals.")
        return self


def workbook_sha256(path: Path) -> str:
    """Hash an input workbook without loading the entire file into memory."""
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_approval_manifest(
    manifest: ApprovalManifest,
    report: ResolutionJobReport,
    *,
    source_path: Path,
) -> dict[tuple[str, str], ResolutionApproval]:
    """Ensure approvals refer to verified candidates in the original job.

    This checks authorization scope and provenance. It does NOT authorize a
    shortcut around fresh country/type verification during resumption.
    """
    if manifest.source_job_id != report.job_id:
        raise ValueError("Approval source job does not match the resolution report.")
    if manifest.workbook_sha256 != workbook_sha256(source_path):
        raise ValueError("Source workbook changed since approval was prepared.")

    operations = {item.operation_id: item for item in report.operations}
    accepted: dict[tuple[str, str], ResolutionApproval] = {}

    for approval in manifest.approvals:
        operation = operations.get(approval.operation_id)
        if operation is None:
            raise ValueError(f"Unknown source operation: {approval.operation_id}.")

        key = (approval.record_id, approval.step_id)
        if not any((link.record_id, link.step_id) == key for link in operation.record_links):
            raise ValueError(f"Approval does not belong to record/step: {key}.")

        source_report = operation.report
        if source_report.decision.status not in {"needs_review", "incomplete_verification"}:
            raise ValueError(f"Approval is not required for record/step: {key}.")

        matches = [
            candidate
            for candidate in source_report.candidate_set.candidates
            if (
                candidate.reference.source == approval.source
                and candidate.reference.entity_id == approval.entity_id
            )
        ]
        if len(matches) != 1 or matches[0].verification != "verified":
            raise ValueError(f"Approval candidate is not source-verified: {key}.")
        if matches[0].entity_type != source_report.candidate_set.query.entity_type:
            raise ValueError(f"Approval candidate does not match the requested type: {key}.")

        accepted[key] = approval

    return accepted
