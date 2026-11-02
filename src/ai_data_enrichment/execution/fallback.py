"""Explicit, provenance-preserving fallback for registered compatible providers.

The coordinator wraps independently versioned and cached primary/secondary
handlers. It never changes source identity, provider URLs, or plan semantics.
"""

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.models import OperationResult, RuntimeValue

FallbackErrorCode = Literal[
    "RETRY_EXHAUSTED",
    "REQUEST_BUDGET_EXCEEDED",
    "RATE_BUDGET_EXCEEDED",
    "PROVIDER_OPERATION_BUDGET_EXCEEDED",
]
_DEFAULT_TRIGGERS: frozenset[FallbackErrorCode] = frozenset(
    {"RETRY_EXHAUSTED", "REQUEST_BUDGET_EXCEEDED", "RATE_BUDGET_EXCEEDED"}
)


class ApprovedFallback(BaseModel):
    """Application-owned, explicit compatibility and alternate-provider contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary_provider_id: str = Field(min_length=1)
    fallback_provider_id: str = Field(min_length=1)
    compatibility_contract_id: str = Field(min_length=1)
    fallback_configuration_id: str = Field(min_length=1)
    fallback_provider_version: str = Field(min_length=1)
    fallback_evidence_source: str = Field(min_length=1)
    trigger_error_codes: frozenset[FallbackErrorCode] = Field(
        default_factory=lambda: _DEFAULT_TRIGGERS
    )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        values = (
            self.primary_provider_id,
            self.fallback_provider_id,
            self.compatibility_contract_id,
            self.fallback_configuration_id,
            self.fallback_provider_version,
            self.fallback_evidence_source,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Fallback provider and contract IDs must not be blank.")
        if self.primary_provider_id == self.fallback_provider_id:
            raise ValueError("Fallback provider must differ from selected primary provider.")
        if not self.trigger_error_codes:
            raise ValueError("Fallback must have explicit transient terminal triggers.")
        return self


class FallbackStats:
    """Mutable per-job accounting, separate from physical request metrics."""

    def __init__(self) -> None:
        self.attempts = 0
        self.successes = 0


class _EvidenceCheckedFallback:
    """Reject unevidenced alternative successes before either cache can store them."""

    def __init__(self, handler: CapabilityHandler, *, source: str) -> None:
        self._handler = handler
        self._source = source

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        result = self._handler.execute(inputs=inputs, parameters=parameters)
        if result.status == "success" and not any(
            evidence.source == self._source for evidence in result.evidence
        ):
            return OperationResult(
                status="failure",
                error_code="FALLBACK_EVIDENCE_MISSING",
                message="Approved fallback returned no verifiable source evidence.",
            )
        return result


class _FallbackCoordinator:
    """Invoke the alternative only after an approved terminal primary failure."""

    def __init__(
        self,
        *,
        primary: CapabilityHandler,
        alternate: CapabilityHandler,
        approved: ApprovedFallback,
        stats: FallbackStats,
    ) -> None:
        self._primary = primary
        self._alternate = alternate
        self._approved = approved
        self._stats = stats

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        primary_result = self._primary.execute(inputs=inputs, parameters=parameters)
        if (
            primary_result.status == "success"
            or primary_result.error_code not in self._approved.trigger_error_codes
        ):
            return primary_result

        self._stats.attempts += 1
        alternate_result = self._alternate.execute(inputs=inputs, parameters=parameters)
        if alternate_result.status == "success":
            self._stats.successes += 1
            return alternate_result
        # Preserve the primary's terminal error; never fabricate a success.
        return primary_result


def validate_compatibility(
    *,
    capability_id: str,
    approved: ApprovedFallback,
    selected_provider: str,
    semantic_contracts: Mapping[tuple[str, str], str],
) -> None:
    """Preflight explicit app-maintained compatibility declarations, not heuristics."""
    if approved.primary_provider_id != selected_provider:
        raise ValueError(f"Fallback selected provider mismatch for capability {capability_id}.")
    primary = semantic_contracts.get((capability_id, selected_provider))
    alternate = semantic_contracts.get((capability_id, approved.fallback_provider_id))
    if (
        not primary
        or not alternate
        or primary != approved.compatibility_contract_id
        or alternate != approved.compatibility_contract_id
    ):
        raise ValueError(
            f"No approved equivalent output/source contract for fallback of {capability_id}."
        )
