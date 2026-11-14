"""What each plan step has done over the rows finished so far."""

from collections.abc import Iterable
from typing import Literal

from ai_data_enrichment.execution.models import OperationResult, RowExecutionResult

StepOutcomeKind = Literal["filled", "failed", "skipped"]
# The order the three counts are kept and shared in.
OUTCOME_KINDS: tuple[StepOutcomeKind, ...] = ("filled", "failed", "skipped")


def classify_step(result: OperationResult) -> StepOutcomeKind:
    """A step either gave a value, was not reached, or gave none itself."""
    if result.status == "success":
        return "filled"
    return "skipped" if result.error_code == "DEPENDENCY_FAILED" else "failed"


class StepTally:
    """Counts only: per step, how many finished rows it filled, failed or skipped."""

    def __init__(self, step_ids: Iterable[str]) -> None:
        self._counts: dict[str, dict[str, int]] = {
            step_id: dict.fromkeys(OUTCOME_KINDS, 0) for step_id in step_ids
        }
        self._rows = 0

    def add(self, row: RowExecutionResult) -> None:
        """Count one finished row; a step the row holds no result for is not counted."""
        self._rows += 1
        for step in row.steps:
            counts = self._counts.get(step.step_id)
            if counts is not None:
                counts[classify_step(step.result)] += 1

    def snapshot(self) -> dict[str, dict[str, int]]:
        """The counts so far; empty until a row has finished."""
        if self._rows == 0:
            return {}
        return {step_id: dict(counts) for step_id, counts in self._counts.items()}
