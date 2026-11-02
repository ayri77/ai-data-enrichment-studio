"""Source-independent, typed contracts for bounded multi-row execution.

These models define the job boundary; they do not execute plans or cache calls.
"""

from collections.abc import Iterable
from math import isfinite
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_data_enrichment.execution.models import RowExecutionResult, RuntimeValue
from ai_data_enrichment.providers.request_policy import ProviderRequestCounters


class InputRecord(BaseModel):
    """Associate normalized row values with a stable job-local record identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    record_id: str = Field(min_length=1)
    values: dict[str, RuntimeValue | None]

    @field_validator("record_id")
    @classmethod
    def validate_record_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Record ID must not be blank.")
        return value

    @field_validator("values")
    @classmethod
    def validate_column_names(
        cls, values: dict[str, RuntimeValue | None]
    ) -> dict[str, RuntimeValue | None]:
        if any(not name.strip() for name in values):
            raise ValueError("Input column names must not be blank.")
        return values


class JobExecutionPolicy(BaseModel):
    """Bound accepted rows, chunk size and job-scoped successful result reuse."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_rows: int = Field(default=1000, gt=0, strict=True)
    batch_size: int = Field(default=100, gt=0, strict=True)
    # Zero disables storing memoized results; it does not prevent execution.
    max_memoized_results: int = Field(default=1000, ge=0, strict=True)
    # Each tuple is an approved (capability_id, provider_id) binding.
    memoizable_providers: frozenset[tuple[str, str]] = Field(default_factory=frozenset)
    # Persistent eligibility is opt-in per approved (capability, provider) pair.
    persistent_cache_ttls: dict[tuple[str, str], float] = Field(default_factory=dict)

    @field_validator("memoizable_providers")
    @classmethod
    def validate_provider_bindings(
        cls, bindings: frozenset[tuple[str, str]]
    ) -> frozenset[tuple[str, str]]:
        if any(not capability.strip() or not provider.strip() for capability, provider in bindings):
            raise ValueError("Memoizable capability and provider IDs must not be blank.")
        return bindings

    @model_validator(mode="after")
    def validate_persistent_cache_ttls(self) -> Self:
        for binding, ttl in self.persistent_cache_ttls.items():
            if binding not in self.memoizable_providers:
                raise ValueError("Persistent cache TTL requires an approved memoizable provider.")
            if not isfinite(ttl) or ttl <= 0:
                raise ValueError("Persistent cache TTL must be positive and finite.")
        return self


class RecordExecution(BaseModel):
    """Link an existing per-row DAG result to its original input record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    row_result: RowExecutionResult

    @field_validator("record_id")
    @classmethod
    def validate_record_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Record ID must not be blank.")
        return value


class JobExecutionMetrics(BaseModel):
    """Keep row outcomes, handler invocations and in-job reuse distinguishable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_rows: int = Field(default=0, ge=0, strict=True)
    successful_rows: int = Field(default=0, ge=0, strict=True)
    partial_rows: int = Field(default=0, ge=0, strict=True)
    failed_rows: int = Field(default=0, ge=0, strict=True)

    fallback_attempts: int = Field(default=0, ge=0, strict=True)
    fallback_successes: int = Field(default=0, ge=0, strict=True)
    logical_operations: int = Field(default=0, ge=0, strict=True)
    executed_handler_calls: int = Field(default=0, ge=0, strict=True)
    reused_operations: int = Field(default=0, ge=0, strict=True)
    persistent_cache_hits: int = Field(default=0, ge=0, strict=True)
    persistent_cache_misses: int = Field(default=0, ge=0, strict=True)
    persistent_cache_writes: int = Field(default=0, ge=0, strict=True)
    persistent_cache_invalid_entries: int = Field(default=0, ge=0, strict=True)
    memoized_results: int = Field(default=0, ge=0, strict=True)
    memo_capacity_skips: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def validate_totals(self) -> Self:
        if self.fallback_successes > self.fallback_attempts:
            raise ValueError("Fallback successes cannot exceed attempted fallbacks.")
        if self.fallback_attempts > self.logical_operations:
            raise ValueError("Fallback attempts cannot exceed logical operations.")
        if self.successful_rows + self.partial_rows + self.failed_rows != self.total_rows:
            raise ValueError("Row outcome counts must sum to total_rows.")
        accounted = (
            self.executed_handler_calls + self.reused_operations + self.persistent_cache_hits
        )
        if accounted != self.logical_operations:
            raise ValueError(
                "Handler calls, memory reuse and SQLite hits must sum to logical_operations."
            )
        successful_sources = self.executed_handler_calls + self.persistent_cache_hits
        if self.memoized_results > successful_sources:
            raise ValueError("Retained memoized results cannot exceed successful result sources.")
        if self.memo_capacity_skips > successful_sources:
            raise ValueError("Memo capacity skips cannot exceed successful result sources.")
        if self.persistent_cache_invalid_entries > self.persistent_cache_misses:
            raise ValueError("Invalid cache entries cannot exceed persistent cache misses.")
        if self.persistent_cache_writes > self.executed_handler_calls:
            raise ValueError("Persistent cache writes cannot exceed handler calls.")
        return self


def classify_row_outcome(row: RowExecutionResult) -> str:
    """Classify a row without hiding successes in partially failed DAGs."""
    successful_steps = sum(step.result.status == "success" for step in row.steps)

    if successful_steps == len(row.steps) and row.steps:
        return "success"
    if successful_steps:
        return "partial"
    return "failure"


class JobExecutionResult(BaseModel):
    """Ordered record outcomes and internally consistent aggregate counters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    records: tuple[RecordExecution, ...]
    metrics: JobExecutionMetrics
    provider_requests: dict[str, ProviderRequestCounters] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_records_and_metrics(self) -> Self:
        identifiers = [record.record_id for record in self.records]

        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Record IDs must be unique within a job.")
        if self.metrics.total_rows != len(self.records):
            raise ValueError("Job metrics total_rows must match record count.")

        actual = [classify_row_outcome(record.row_result) for record in self.records]
        counts = {
            "success": self.metrics.successful_rows,
            "partial": self.metrics.partial_rows,
            "failure": self.metrics.failed_rows,
        }
        if any(actual.count(status) != count for status, count in counts.items()):
            raise ValueError("Job row outcome metrics do not match record results.")
        return self


def prepare_input_records(
    records: Iterable[InputRecord],
    policy: JobExecutionPolicy,
) -> tuple[InputRecord, ...]:
    """Preflight at most max_rows + 1 records before executing any provider.

    A finite row limit bounds consumption even for one-pass or infinite sources.
    """
    prepared: list[InputRecord] = []
    seen_ids: set[str] = set()

    for index, record in enumerate(records):
        if index >= policy.max_rows:
            raise ValueError(f"Input exceeds max_rows={policy.max_rows}.")
        if not isinstance(record, InputRecord):
            raise TypeError("Job input records must be InputRecord instances.")
        if record.record_id in seen_ids:
            raise ValueError(f"Duplicate record ID: {record.record_id!r}.")

        seen_ids.add(record.record_id)
        prepared.append(record)

    return tuple(prepared)
