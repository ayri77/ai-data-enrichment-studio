"""What each plan step has done over the rows finished so far."""

from collections.abc import Iterable
from typing import Literal

from ai_data_enrichment.execution.models import OperationResult, RowExecutionResult
from ai_data_enrichment.providers.text_extraction import EXTRACTION_CAPABILITY
from ai_data_enrichment.providers.text_extraction_reply import OUTCOME_NOT_APPLICABLE

StepOutcomeKind = Literal["filled", "failed", "skipped", "not_applicable"]
# The order the three counts are kept and shared in.
OUTCOME_KINDS: tuple[StepOutcomeKind, ...] = ("filled", "failed", "skipped")
# A plan that reads texts has a fourth count: a text that is not about the task.
NOT_APPLICABLE_KIND: StepOutcomeKind = "not_applicable"
EXTRACTION_OUTCOME_KINDS: tuple[StepOutcomeKind, ...] = (*OUTCOME_KINDS, NOT_APPLICABLE_KIND)


def outcome_kinds(*, extraction: bool) -> tuple[StepOutcomeKind, ...]:
    """The counts a plan keeps per step: the fourth one only for a plan that reads texts."""
    return EXTRACTION_OUTCOME_KINDS if extraction else OUTCOME_KINDS


def is_settled_not_applicable(capability: str, result: OperationResult) -> bool:
    """A text the model says is not about the task: a settled outcome, not a problem.

    Narrow on purpose: only the capability ``text_extraction`` and only the code
    ``NOT_APPLICABLE``. Every other failure of it, and every failure of any other
    capability, is counted as it always was.
    """
    return (
        capability == EXTRACTION_CAPABILITY
        and result.status == "failure"
        and result.error_code == OUTCOME_NOT_APPLICABLE
    )


def classify_step(result: OperationResult) -> StepOutcomeKind:
    """A step either gave a value, was not reached, or gave none itself."""
    if result.status == "success":
        return "filled"
    return "skipped" if result.error_code == "DEPENDENCY_FAILED" else "failed"


class StepTally:
    """Counts only: per step, how many finished rows it filled, failed or skipped."""

    def __init__(self, step_ids: Iterable[str], *, extraction_steps: Iterable[str] = ()) -> None:
        """``extraction_steps``: the steps of the capability ``text_extraction``. A plan with
        one counts a fourth number for them, ``not_applicable``; other plans have three."""
        self._extraction = frozenset(extraction_steps)
        self.kinds = outcome_kinds(extraction=bool(self._extraction))
        self._counts: dict[str, dict[str, int]] = {
            step_id: dict.fromkeys(self.kinds, 0) for step_id in step_ids
        }
        self._rows = 0

    def add(self, row: RowExecutionResult) -> None:
        """Count one finished row; a step the row holds no result for is not counted."""
        self._rows += 1
        for step in row.steps:
            counts = self._counts.get(step.step_id)
            if counts is None:
                continue
            if step.step_id in self._extraction and is_settled_not_applicable(
                EXTRACTION_CAPABILITY, step.result
            ):
                counts[NOT_APPLICABLE_KIND] += 1
            else:
                counts[classify_step(step.result)] += 1

    def snapshot(self) -> dict[str, dict[str, int]]:
        """The counts so far; empty until a row has finished."""
        if self._rows == 0:
            return {}
        return {step_id: dict(counts) for step_id, counts in self._counts.items()}
