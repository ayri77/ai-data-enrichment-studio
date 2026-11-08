"""Audited, policy-keyed execution bridge for existing DAG capability handlers.

The ordinary JobExecutor still sees OperationResult; the paired report cannot
be lost on memo/cache hits and is captured for every invoked step and source row.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from json import dumps
from typing import Protocol, cast

from ai_data_enrichment.domain.enrichment_plan import PlanStep
from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.job_models import JobExecutionPolicy
from ai_data_enrichment.execution.memoization import MemoCapacity
from ai_data_enrichment.execution.models import OperationResult, RuntimeValue
from ai_data_enrichment.execution.operation_keys import persistent_key_digest
from ai_data_enrichment.resolution.models import (
    CandidateSet,
    ResolutionAttribute,
    ResolutionDecision,
    ResolutionJobReport,
    ResolutionOperationAudit,
    ResolutionPolicy,
    ResolutionQuery,
    ResolutionRecordLink,
    ResolutionReport,
)
from ai_data_enrichment.resolution.sqlite_cache import (
    ResolutionCacheHit,
    SQLiteResolutionCache,
    validate_paired_result,
)

_RESOLVER_VERSION = "audited-resolution-engine:v1"
_APPROVED_PARAMETERS = {
    "resolve_entity": {"entity_type": "mountain"},
}

_GEOCODE_PARAMETERS = (
    {},
    {"required_role": "country_capital"},
)


class ResolutionOutcome(Protocol):
    @property
    def operation(self) -> OperationResult: ...

    @property
    def report(self) -> ResolutionReport: ...


class ResolutionService(Protocol):
    """Existing Open-Meteo and Wikidata services satisfy this structurally."""

    def resolve(
        self, *, inputs: Mapping[str, RuntimeValue], policy: ResolutionPolicy
    ) -> ResolutionOutcome: ...


class ParameterizedResolutionService(ResolutionService, Protocol):
    """Resolution service accepting validated step parameters."""

    def resolve(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        policy: ResolutionPolicy,
        parameters: Mapping[str, str] | None = None,
    ) -> ResolutionOutcome: ...


class ApprovedResolutionService(ResolutionService, Protocol):
    """Contract for a user-approved resolution service."""

    def validate_step(
        self,
        step: PlanStep,
        *,
        provider_id: str,
    ) -> None: ...

    def validate_inputs(
        self,
        inputs: Mapping[str, RuntimeValue],
    ) -> None: ...


def effective_configuration_id(configuration_id: str, policy: ResolutionPolicy) -> str:
    """Include *all* policy fields, schema and engine version in key identity."""
    if not configuration_id.strip():
        raise ValueError("Audited configuration ID must not be blank.")
    canonical = dumps(
        [_RESOLVER_VERSION, policy.model_dump(mode="json")],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{configuration_id}|{_RESOLVER_VERSION}|{sha256(canonical.encode()).hexdigest()}"


class _ResolutionInvoker:
    """Call an approved resolution provider and retain its paired report."""

    def __init__(
        self,
        service: ResolutionService,
        policy: ResolutionPolicy,
        capability_id: str,
    ) -> None:
        self.service = service
        self.policy = policy
        self.capability_id = capability_id
        self.latest: ResolutionCacheHit | None = None

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        self.latest = None

        if self.capability_id == "geocode":
            service = cast(ParameterizedResolutionService, self.service)
            result = service.resolve(
                inputs=inputs,
                policy=self.policy,
                parameters=parameters,
            )
        else:
            result = self.service.resolve(
                inputs=inputs,
                policy=self.policy,
            )

        self.latest = ResolutionCacheHit(
            operation=result.operation,
            report=result.report,
        )
        return result.operation


@dataclass(frozen=True)
class ResolutionMemoMetrics:
    logical: int
    executed: int
    reused: int
    persistent_hits: int
    persistent_misses: int
    persistent_writes: int
    persistent_invalid: int
    memoized: int
    capacity_skips: int


class AuditedResolutionHandler:
    """Cache *paired* approved successes and expose the last source decision."""

    def __init__(
        self,
        *,
        capability_id: str,
        provider_id: str,
        configuration_id: str,
        capability_version: str,
        provider_version: str,
        service: ResolutionService,
        policy: ResolutionPolicy,
        job_policy: JobExecutionPolicy,
        shared_capacity: MemoCapacity,
        persistent_cache: SQLiteResolutionCache | None,
        wrap_provider: "ProviderWrapper | None" = None,
    ) -> None:
        if capability_id not in {*_APPROVED_PARAMETERS, "geocode"}:
            raise ValueError("Unsupported audited resolution capability.")
        if not capability_version.strip() or not provider_version.strip():
            raise ValueError("Audited resolution requires explicit capability/provider versions.")
        if not provider_id.strip():
            raise ValueError("Audited resolution requires an approved provider.")
        self.capability_id = capability_id
        self.provider_id = provider_id
        self._configuration_id = effective_configuration_id(configuration_id, policy)
        self._capability_version = capability_version
        self._provider_version = provider_version
        self.policy = policy
        self._job_policy = job_policy
        self._shared_capacity = shared_capacity
        self._cache = persistent_cache
        self._invoker = _ResolutionInvoker(
            service,
            policy,
            capability_id,
        )
        self._provider_handler: CapabilityHandler = (
            wrap_provider(self._invoker) if wrap_provider is not None else self._invoker
        )
        self._memory: dict[str, ResolutionCacheHit] = {}
        self.last: ResolutionCacheHit | None = None
        self.last_operation_id: str | None = None
        self._logical = 0
        self._executed = 0
        self._reused = 0
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._invalid = 0
        self._skips = 0

    def execute(
        self, *, inputs: Mapping[str, RuntimeValue], parameters: Mapping[str, str]
    ) -> OperationResult:
        if self.capability_id == "geocode":
            valid = dict(parameters) in _GEOCODE_PARAMETERS
        else:
            valid = dict(parameters) == _APPROVED_PARAMETERS[self.capability_id]

        if not valid:
            raise ValueError("Unexpected parameters for audited resolution.")

        digest = persistent_key_digest(
            capability_id=self.capability_id,
            capability_version=self._capability_version,
            provider_id=self.provider_id,
            provider_version=self._provider_version,
            configuration_id=self._configuration_id,
            inputs=inputs,
            parameters=parameters,
        )
        if digest is None:
            raise ValueError("Unsupported audited runtime input types.")
        self.last_operation_id = digest
        self.last = None
        self._logical += 1
        binding = (self.capability_id, self.provider_id)
        memo_allowed = binding in self._job_policy.memoizable_providers
        ttl = self._job_policy.persistent_cache_ttls.get(binding)
        if memo_allowed and digest in self._memory:
            self._reused += 1
            self.last = self._memory[digest]
            return self.last.operation

        if ttl is not None:
            if self._cache is None:
                raise ValueError("Audited persistent cache is not configured.")
            lookup = self._cache.get(digest, provider_id=self.provider_id)
            if lookup.status == "hit":
                assert lookup.value is not None
                if lookup.value.report.policy != self.policy:
                    raise ValueError("Resolution cache returned the wrong decision policy.")
                self._hits += 1
                self.last = lookup.value
                self._retain(digest, lookup.value, memo_allowed)
                return lookup.value.operation
            self._misses += 1
            if lookup.status == "invalid":
                self._invalid += 1

        self._executed += 1
        self._invoker.latest = None
        result = self._provider_handler.execute(inputs=inputs, parameters=parameters)
        pair = self._invoker.latest
        if pair is None:
            pair = ResolutionCacheHit(
                operation=result,
                report=_blocked_operation_report(
                    inputs=inputs,
                    capability_id=self.capability_id,
                    provider_id=self.provider_id,
                    policy=self.policy,
                    failure=result,
                ),
            )
        if pair.operation != result:
            raise ValueError("Provider result and retained audit disagree.")
        if pair.report.policy != self.policy:
            raise ValueError("Provider returned a report under a different policy.")
        if pair.report.candidate_set.provider_id != self.provider_id:
            raise ValueError("Provider returned a report under a different provider identity.")
        if result.status == "success":
            validate_paired_result(result, pair.report, provider_id=self.provider_id)
            if ttl is not None:
                assert self._cache is not None
                self._cache.put(
                    digest,
                    pair,
                    provider_id=self.provider_id,
                    ttl_seconds=ttl,
                )
                self._writes += 1
            self._retain(digest, pair, memo_allowed)
        elif pair.report.decision.selected is not None:
            raise ValueError("Failed operation cannot contain a resolved decision.")
        self.last = pair
        return result

    def _retain(self, digest: str, pair: ResolutionCacheHit, allowed: bool) -> None:
        if not allowed:
            return
        if self._shared_capacity.reserve():
            self._memory[digest] = pair
        else:
            self._skips += 1

    def snapshot(self) -> ResolutionMemoMetrics:
        return ResolutionMemoMetrics(
            logical=self._logical,
            executed=self._executed,
            reused=self._reused,
            persistent_hits=self._hits,
            persistent_misses=self._misses,
            persistent_writes=self._writes,
            persistent_invalid=self._invalid,
            memoized=len(self._memory),
            capacity_skips=self._skips,
        )


class ProviderWrapper(Protocol):
    def __call__(self, handler: CapabilityHandler) -> CapabilityHandler: ...


def _blocked_operation_report(
    *,
    inputs: Mapping[str, RuntimeValue],
    capability_id: str,
    provider_id: str,
    policy: ResolutionPolicy,
    failure: OperationResult,
) -> ResolutionReport:
    """Report budget admission failures without inventing a selected entity."""
    if failure.status != "failure" or failure.error_code is None:
        raise ValueError("Provider blocked execution without a typed failure.")
    label = inputs.get("city" if capability_id == "geocode" else "name")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("Blocked resolution has no valid lookup name.")
    country = inputs.get("country")
    context = (
        (ResolutionAttribute(name="country", value=country),)
        if isinstance(country, str) and country.strip()
        else ()
    )
    return ResolutionReport(
        candidate_set=CandidateSet(
            query=ResolutionQuery(
                name=label,
                entity_type="city" if capability_id == "geocode" else "mountain",
                context=context,
            ),
            provider_id=provider_id,
            search_complete=False,
            verification_complete=False,
            provider_error_code=failure.error_code,
        ),
        policy=policy,
        decision=ResolutionDecision(
            status="provider_failure",
            reason_codes=("operation_admission_denied",),
            explanation="Provider operation budget prevented a source request.",
        ),
    )


class ResolutionAuditCollector:
    """Merge identical reports while keeping every row+step reference."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[ResolutionReport, list[ResolutionRecordLink]]] = {}

    def add(
        self, *, operation_key: str, report: ResolutionReport, record_id: str, step_id: str
    ) -> None:
        # Include the report's content: repeated failures may yield different
        # candidate sets if a provider changes between attempts.
        report_hash = sha256(report.model_dump_json().encode()).hexdigest()
        identity = sha256(f"{operation_key}|{report_hash}".encode()).hexdigest()
        link = ResolutionRecordLink(record_id=record_id, step_id=step_id)
        previous = self._entries.get(identity)
        if previous is None:
            self._entries[identity] = (report, [link])
        else:
            previous[1].append(link)

    def finalize(
        self,
        *,
        job_id: str,
        previous_job_id: str | None,
        total_rows: int,
    ) -> ResolutionJobReport:
        operations = tuple(
            ResolutionOperationAudit(
                operation_id=identifier,
                report=report,
                record_links=tuple(links),
            )
            for identifier, (report, links) in self._entries.items()
        )
        decisions = [entry.report.decision.status for entry in operations]
        if "provider_failure" in decisions:
            status = "failed"
        elif any(
            state in {"needs_review", "unresolved", "incomplete_verification"}
            for state in decisions
        ):
            status = "awaiting_review"
        else:
            status = "completed"
        return ResolutionJobReport(
            job_id=job_id,
            previous_job_id=previous_job_id,
            status=status,
            total_rows=total_rows,
            processed_rows=total_rows,
            operations=operations,
        )
