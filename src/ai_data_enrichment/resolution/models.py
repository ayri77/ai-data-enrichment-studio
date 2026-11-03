"""Typed, serializable Resolution Foundation contracts (no runtime integration yet).

Provider adapters will populate candidates; the shared decision layer will
populate reports. These models deliberately do not change OperationResult,
the DAG executor or success-only memoization in this first slice.
"""

from math import isfinite
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from ai_data_enrichment.domain.capability import Identifier
from ai_data_enrichment.execution.models import EntityRef, Evidence

ResolutionValue = StrictStr | StrictInt | StrictFloat | StrictBool
VerificationState = Literal["verified", "rejected", "unchecked"]
ResolutionStatus = Literal[
    "resolved",
    "auto_resolved",
    "needs_review",
    "unresolved",
    "incomplete_verification",
    "provider_failure",
]
SelectionBasis = Literal["unique_verified", "ranking_policy", "user_override"]
JobReviewStatus = Literal["completed", "awaiting_review", "cancelled", "failed"]


class _ResolutionModel(BaseModel):
    """Strict, immutable payloads suitable for audit and JSON serialization."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _nonempty(value: str) -> str:
    if not value.strip():
        raise ValueError("Value must not be blank.")
    return value


def _unique_attributes(attributes: tuple["ResolutionAttribute", ...]) -> bool:
    names = [attribute.name for attribute in attributes]
    return len(names) == len(set(names))


class ResolutionAttribute(_ResolutionModel):
    """Typed semantic attribute; missing/unknown attributes are omitted."""

    name: Identifier
    value: ResolutionValue
    evidence: tuple[Evidence, ...] = ()

    @field_validator("value")
    @classmethod
    def check_value(cls, value: ResolutionValue) -> ResolutionValue:
        if isinstance(value, str) and not value.strip():
            raise ValueError("Attribute value must not be blank.")
        if type(value) is float and not isfinite(value):
            raise ValueError("Numeric attribute value must be finite.")
        return value


class ResolutionQuery(_ResolutionModel):
    """Original lookup intent plus *recognized* typed contextual attributes.

    This model does not authorize unregistered inputs to be passed to a
    provider. CapabilitySpec and PlanValidator remain the input gate.
    """

    name: str = Field(min_length=1, max_length=512)
    entity_type: Identifier
    context: tuple[ResolutionAttribute, ...] = ()

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        return _nonempty(value)

    @model_validator(mode="after")
    def check_context(self) -> Self:
        if not _unique_attributes(self.context):
            raise ValueError("Resolution query has duplicate context keys.")
        return self


class ResolutionCandidate(_ResolutionModel):
    """Source-qualified candidate and typed provider-normalized facts."""

    reference: EntityRef
    label: str = Field(min_length=1, max_length=512)
    entity_type: Identifier
    verification: VerificationState = "unchecked"
    attributes: tuple[ResolutionAttribute, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    reason_codes: tuple[Identifier, ...] = ()

    @field_validator("label")
    @classmethod
    def check_label(cls, value: str) -> str:
        return _nonempty(value)

    @model_validator(mode="after")
    def check_candidate(self) -> Self:
        if not _unique_attributes(self.attributes):
            raise ValueError("Candidate has duplicate attribute keys.")
        if self.verification == "verified" and not self.evidence:
            raise ValueError("Verified candidate requires source evidence.")
        return self


class CandidateSet(_ResolutionModel):
    """Bounded provider results with explicit retrieval/verification completeness."""

    query: ResolutionQuery
    provider_id: str = Field(min_length=1)
    candidates: tuple[ResolutionCandidate, ...] = ()
    search_complete: bool
    verification_complete: bool
    incomplete_reason: str | None = None
    provider_error_code: str | None = None

    @field_validator("provider_id")
    @classmethod
    def check_provider(cls, value: str) -> str:
        return _nonempty(value)

    @model_validator(mode="after")
    def check_completeness(self) -> Self:
        ids = [(c.reference.source, c.reference.entity_id) for c in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate candidate source/entity identity.")
        if self.search_complete and self.verification_complete:
            if self.incomplete_reason is not None or self.provider_error_code is not None:
                raise ValueError("A complete candidate set cannot report partial retrieval.")
        elif not (self.incomplete_reason or self.provider_error_code):
            raise ValueError("Incomplete candidate set requires a reason or provider error.")
        for value in (self.incomplete_reason, self.provider_error_code):
            if value is not None:
                _nonempty(value)
        return self


class CandidateAssessment(_ResolutionModel):
    """Decision-layer interpretation of a candidate, not provider source data."""

    reference: EntityRef
    eligibility: Literal["eligible", "excluded", "unchecked"]
    rank: int | None = Field(default=None, ge=1)
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_rank(self) -> Self:
        if self.rank is not None and self.eligibility != "eligible":
            raise ValueError("Only eligible candidates may have a rank.")
        return self


class ResolutionPolicy(_ResolutionModel):
    """Versioned decision configuration; request budgets are configured elsewhere."""

    policy_id: Identifier
    version: str = Field(min_length=1)
    strategy: Literal["strict", "ranked"] = "strict"
    auto_accept_ranked: bool = False
    prefer_prominent: bool = False

    @field_validator("version")
    @classmethod
    def check_version(cls, value: str) -> str:
        return _nonempty(value)

    @model_validator(mode="after")
    def check_strategy(self) -> Self:
        if self.strategy == "strict" and (self.auto_accept_ranked or self.prefer_prominent):
            raise ValueError("Strict policy cannot enable ranked decisions.")
        return self


class ResolutionDecision(_ResolutionModel):
    """Outcome independent of technical OperationResult success/failure."""

    status: ResolutionStatus
    selected: EntityRef | None = None
    basis: SelectionBasis | None = None
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    explanation: str = Field(min_length=1)

    @field_validator("explanation")
    @classmethod
    def check_explanation(cls, value: str) -> str:
        return _nonempty(value)

    @model_validator(mode="after")
    def check_decision(self) -> Self:
        if self.status in {"resolved", "auto_resolved"}:
            if self.selected is None or self.basis is None:
                raise ValueError("Resolved decision needs selected identity and basis.")
            if (self.status == "auto_resolved") != (self.basis == "ranking_policy"):
                raise ValueError("Ranking selection must use auto_resolved status.")
        elif self.selected is not None or self.basis is not None:
            raise ValueError("Unresolved/review decisions cannot select a final entity.")
        return self


class ResolutionReport(_ResolutionModel):
    """Versioned machine-readable evidence/decision report for one operation."""

    schema_version: Literal["1.0"] = "1.0"
    candidate_set: CandidateSet
    policy: ResolutionPolicy
    decision: ResolutionDecision
    assessments: tuple[CandidateAssessment, ...] = ()

    @model_validator(mode="after")
    def check_report(self) -> Self:
        candidate_set = self.candidate_set
        decision = self.decision
        is_complete = candidate_set.search_complete and candidate_set.verification_complete
        candidate_ids = {c.reference for c in candidate_set.candidates}
        assessed_ids = [assessment.reference for assessment in self.assessments]
        if len(assessed_ids) != len(set(assessed_ids)):
            raise ValueError("Candidate assessments must not repeat identities.")
        if any(ref not in candidate_ids for ref in assessed_ids):
            raise ValueError("Assessment refers to an unknown candidate.")

        if decision.selected is not None:
            selected_candidates = [
                candidate
                for candidate in candidate_set.candidates
                if candidate.reference == decision.selected
            ]
            if not selected_candidates or selected_candidates[0].verification != "verified":
                raise ValueError("Selected entity must be a verified listed candidate.")
            # An explicit user decision may override an *incomplete search*,
            # but never skips verification of the chosen candidate itself.
            if not is_complete and decision.basis != "user_override":
                raise ValueError("Incomplete verification cannot be auto-accepted.")

        if decision.status == "auto_resolved":
            if self.policy.strategy != "ranked" or not self.policy.auto_accept_ranked:
                raise ValueError("Auto-selection requires an enabled ranked policy.")
            winners = [
                assessment
                for assessment in self.assessments
                if assessment.eligibility == "eligible" and assessment.rank == 1
            ]
            if len(winners) != 1 or winners[0].reference != decision.selected:
                raise ValueError("Auto-selection requires one explained top-ranked candidate.")

        if decision.status == "unresolved" and not is_complete:
            raise ValueError("A no-match result requires completed verification.")

        if decision.status == "incomplete_verification" and is_complete:
            raise ValueError("Incomplete outcome requires an incomplete candidate set.")

        if decision.status == "provider_failure" and not candidate_set.provider_error_code:
            raise ValueError("Provider failure requires a provider error code.")

        if candidate_set.provider_error_code and decision.status != "provider_failure":
            raise ValueError("Provider error cannot be reported as a normal outcome.")

        if decision.status == "needs_review" and not candidate_set.candidates:
            raise ValueError("Review requires at least one candidate.")

        return self


class ResolutionRecordLink(_ResolutionModel):
    """One consuming record/step; repeated operations may share a report."""

    record_id: str = Field(min_length=1)
    step_id: Identifier

    @field_validator("record_id")
    @classmethod
    def check_record_id(cls, value: str) -> str:
        return _nonempty(value)


class ResolutionOperationAudit(_ResolutionModel):
    """One operation-level report linked to every affected record and DAG step."""

    operation_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    report: ResolutionReport
    record_links: tuple[ResolutionRecordLink, ...] = Field(min_length=1)

    @field_validator("operation_id")
    @classmethod
    def check_operation_id(cls, value: str) -> str:
        return _nonempty(value)

    @model_validator(mode="after")
    def check_links(self) -> Self:
        links = [(link.record_id, link.step_id) for link in self.record_links]
        if len(links) != len(set(links)):
            raise ValueError("Duplicate record/step audit link.")
        return self


class ResolutionJobReport(_ResolutionModel):
    """Review manifest envelope; not yet wired to JobExecutor (M3 ownership).

    A cancelled job may report fewer processed than total rows. The second
    pass may reference the previous job while retaining separate revisions.
    """

    schema_version: Literal["1.0"] = "1.0"
    job_id: str = Field(min_length=1)
    previous_job_id: str | None = None
    status: JobReviewStatus
    total_rows: int = Field(ge=0)
    processed_rows: int = Field(ge=0)
    operations: tuple[ResolutionOperationAudit, ...] = ()

    @field_validator("job_id", "previous_job_id")
    @classmethod
    def check_job_id(cls, value: str | None) -> str | None:
        if value is not None:
            return _nonempty(value)
        return value

    @model_validator(mode="after")
    def check_summary(self) -> Self:
        if self.processed_rows > self.total_rows:
            raise ValueError("Processed rows cannot exceed total rows.")
        if self.previous_job_id == self.job_id:
            raise ValueError("A job cannot be its own previous job.")
        operation_ids = [entry.operation_id for entry in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("Each operation is recorded once per job report.")
        return self
