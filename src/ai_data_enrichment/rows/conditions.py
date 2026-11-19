"""Conditions a candidate has to meet to be taken as a new row."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ai_data_enrichment.rows.errors import RowsError
from ai_data_enrichment.rows.model import Candidate, RowSource

_MOMENT_OPS = ("within_last_days", "on_or_after", "on_or_before")
_TEXT_OPS = ("contains",)


@dataclass(frozen=True)
class Condition:
    """``field_id`` compared by ``op`` with ``value``.

    Moment fields: ``within_last_days`` (int >= 1), ``on_or_after`` and ``on_or_before`` (a
    date). Text fields: ``contains`` (a non-empty str, compared ignoring case).
    """

    field_id: str
    op: str
    value: int | str | date


def _is_bad(condition: Condition) -> bool:
    value = condition.value
    if condition.op == "within_last_days":
        return not isinstance(value, int) or isinstance(value, bool) or value < 1
    if condition.op in ("on_or_after", "on_or_before"):
        return not isinstance(value, date) or isinstance(value, datetime)
    return not isinstance(value, str) or not value.strip()


def check_conditions(source: RowSource, conditions: Sequence[Condition]) -> None:
    """Raise ``rows_bad_condition`` for an unknown field, an op that does not fit the
    field's kind, or a bad value."""
    for condition in conditions:
        field = source.field(condition.field_id)
        if field is None:
            raise RowsError("rows_bad_condition")
        allowed = _MOMENT_OPS if field.kind == "moment" else _TEXT_OPS
        if condition.op not in allowed or _is_bad(condition):
            raise RowsError("rows_bad_condition")


def _holds(condition: Condition, value: datetime | str | None, now: datetime) -> bool:
    if value is None:
        return False
    operand = condition.value
    if isinstance(value, datetime):
        if condition.op == "within_last_days" and isinstance(operand, int):
            return value >= now - timedelta(days=operand)
        if condition.op == "on_or_after" and isinstance(operand, date):
            return value.date() >= operand
        if condition.op == "on_or_before" and isinstance(operand, date):
            return value.date() <= operand
        return False
    if condition.op == "contains" and isinstance(operand, str):
        return operand.casefold() in value.casefold()
    return False


def passes(candidate: Candidate, conditions: Sequence[Condition], *, now: datetime) -> bool:
    """Whether every condition holds; ``now`` is naive UTC.

    A candidate without a value for a field of a condition does not pass.
    """
    return all(
        _holds(condition, candidate.values.get(condition.field_id), now) for condition in conditions
    )
