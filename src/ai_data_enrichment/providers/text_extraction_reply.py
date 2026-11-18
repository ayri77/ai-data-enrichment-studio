"""Read a text extraction reply strictly: a value is kept only where it can be traced.

The reply must be JSON of exactly the requested shape, or the whole text fails. Within a
well-formed reply each value is judged on its own, by code:

- it needs a quote, and the quote must be in the text that was sent (whitespace
  collapsed);
- a number must be readable from its quote;
- a date is ``literal`` when day, month and year are all in the quote, ``derived`` when
  at least the day or the year is, and dropped otherwise;
- a text is ``literal`` when it is contained in its quote, otherwise ``derived``;
- ``yes_no`` and ``one_of`` values are always ``derived``.

A mark says how a value relates to its quote. It does not say that the text is true or
that the mail is genuine. Nothing is converted, repaired or guessed, and a dropped value
carries only a reason and what the model claimed.
"""

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from ai_data_enrichment.providers.text_extraction import (
    ExtractionColumn,
    ExtractionTask,
    tokens,
)

Mark = Literal["literal", "derived"]
DropReason = Literal[
    "quote_not_in_text", "number_not_in_quote", "date_not_in_quote", "invalid_value"
]

OUTCOME_FOUND = "FOUND"
OUTCOME_NOT_APPLICABLE = "NOT_APPLICABLE"
OUTCOME_NOTHING_FOUND = "NOTHING_FOUND"
# The codes of a failed request are those of the web lookup: INVALID_RESPONSE,
# RESPONSE_INCOMPLETE, PROVIDER_ERROR, SOURCE_ACCESS_DENIED, REQUEST_REFUSED.
SUCCESS_OUTCOMES = (OUTCOME_FOUND, OUTCOME_NOT_APPLICABLE, OUTCOME_NOTHING_FOUND)

MAX_QUOTE = 300
MAX_TEXT_VALUE = 500
NUMBER_TOLERANCE = 0.005


@dataclass(frozen=True)
class KeptValue:
    column: str
    slot: str
    kind: str
    # A string, or a float for a number column; a date is the text YYYY-MM-DD.
    value: str | float
    mark: Mark
    quote: str


@dataclass(frozen=True)
class DroppedValue:
    """What the model claimed and the reader did not keep, with the one reason."""

    column: str
    slot: str
    reason: DropReason
    value: str | float | None
    quote: str | None


@dataclass(frozen=True)
class ExtractionResult:
    """What became of one text: found, not applicable, nothing found, or a failure code."""

    outcome: str
    message: str = ""
    kept: tuple[KeptValue, ...] = ()
    dropped: tuple[DroppedValue, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def failed(self) -> bool:
        return self.outcome not in SUCCESS_OUTCOMES


# --- Month names ---------------------------------------------------------------------

_MONTH_NAMES: dict[int, tuple[str, ...]] = {
    1: ("january jan", "januar jänner jän", "января январь янв", "січня січень січ"),
    2: ("february feb", "februar", "февраля февраль фев февр", "лютого лютий лют"),
    3: ("march mar", "märz mär", "марта март мар", "березня березень бер"),
    4: ("april apr", "april", "апреля апрель апр", "квітня квітень квіт"),
    5: ("may", "mai", "мая май", "травня травень трав"),
    6: ("june jun", "juni", "июня июнь июн", "червня червень черв"),
    7: ("july jul", "juli", "июля июль июл", "липня липень лип"),
    8: ("august aug", "august", "августа август авг", "серпня серпень серп"),
    9: ("september sep sept", "september", "сентября сентябрь сен сент", "вересня вересень вер"),
    10: ("october oct", "oktober okt", "октября октябрь окт", "жовтня жовтень жовт"),
    11: ("november nov", "november", "ноября ноябрь ноя нояб", "листопада листопад лист"),
    12: ("december dec", "dezember dez", "декабря декабрь дек", "грудня грудень груд"),
}
_MONTH_BY_NAME: dict[str, int] = {
    name: month for month, groups in _MONTH_NAMES.items() for g in groups for name in g.split()
}


# --- Numbers -------------------------------------------------------------------------

_NUMBER_TOKEN = re.compile(r"\d[\d.,]*")
_GROUPED = re.compile(r"(?<=\d)[ '’\u00a0\u2009\u202f](?=\d{3}(?!\d))")


def _readings(token: str) -> set[float]:
    """Every value a digit string can be read as: ``1.234`` is 1.234 or 1234."""
    token = token.rstrip(".,")
    dots, commas = token.count("."), token.count(",")
    if dots and commas:
        decimal = "." if token.rfind(".") > token.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        if token.count(decimal) != 1:
            return set()
        return _floats(token.replace(thousands, "").replace(decimal, "."))
    if not dots and not commas:
        return _floats(token)
    separator = "." if dots else ","
    head, *rest = token.split(separator)
    if len(rest) > 1:
        # Only thousands separators: 1.234.567
        return _floats(token.replace(separator, "")) if all(len(g) == 3 for g in rest) else set()
    out = _floats(f"{head}.{rest[0]}")
    if len(rest[0]) == 3 and 1 <= len(head) <= 3 and not head.startswith("0"):
        out |= _floats(head + rest[0])
    return out


def _floats(text: str) -> set[float]:
    try:
        return {float(text)}
    except ValueError:
        return set()


def numbers_in(quote: str) -> set[float]:
    """The numbers a quote can be read as holding, with the sign when a minus stands directly
    before the digits. Thousands separators may be dots, commas or spaces."""
    found: set[float] = set()
    for variant in (quote, _GROUPED.sub("", quote)):
        for match in _NUMBER_TOKEN.finditer(variant):
            values = _readings(match.group())
            before = variant[: match.start()]
            negative = before.endswith(("-", "−")) and not (
                len(before) > 1 and before[-2].isalnum()
            )
            found |= {-v for v in values} if negative else values
    return found


def number_in_quote(value: float, quote: str) -> bool:
    return any(abs(value - found) <= NUMBER_TOLERANCE for found in numbers_in(quote))


# --- Dates ---------------------------------------------------------------------------

_DIGIT_RUN = re.compile(r"(?<!\d)\d+(?!\d)")
_WORD = re.compile(r"[^\W\d_]+")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def date_parts(quote: str, day: int, month: int, year: int) -> tuple[bool, bool, bool]:
    """Whether the year, the month and the day can be found in the quote.

    Year and day are digit runs; the month is a month name or abbreviation, or else a
    digit run. A run is used once, so ``11.11.2026`` holds a day and a month.
    """
    runs = _DIGIT_RUN.findall(quote)

    def take(length_ok: Callable[[int], bool], number: int) -> bool:
        for index, run in enumerate(runs):
            if length_ok(len(run)) and int(run) == number:
                del runs[index]
                return True
        return False

    has_year = take(lambda n: n == 4, year)
    has_day = take(lambda n: n <= 2, day)
    named = month in {_MONTH_BY_NAME.get(w.casefold()) for w in _WORD.findall(quote)}
    has_month = named or take(lambda n: n <= 2, month)
    return has_year, has_month, has_day


# --- The reader ----------------------------------------------------------------------


class _Shape(Exception):
    pass


def _reject_constant(name: str) -> Any:
    raise ValueError(name)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _check_value_shape(slot: str, column: ExtractionColumn, data: object) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {"value", "quote"}:
        raise _Shape(f"{slot}: not an object with value and quote")
    value, quote = data["value"], data["quote"]
    if quote is not None and not isinstance(quote, str):
        raise _Shape(f"{slot}: quote is not text")
    if value is not None:
        if column.kind == "number":
            ok = isinstance(value, int | float) and not isinstance(value, bool)
            if ok:
                try:
                    ok = math.isfinite(float(value))
                except OverflowError:
                    ok = False
        else:
            ok = isinstance(value, str)
        if not ok:
            raise _Shape(f"{slot}: value has the wrong type")
    return data


def _failure(code: str, message: str, response: Any = None) -> ExtractionResult:
    input_tokens, output_tokens = tokens(response) if response is not None else (0, 0)
    return ExtractionResult(code, message, input_tokens=input_tokens, output_tokens=output_tokens)


def _invalid(detail: str, response: Any) -> ExtractionResult:
    return _failure(
        "INVALID_RESPONSE", f"The reply does not have the requested shape: {detail}.", response
    )


def _refused(response: Any) -> bool:
    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                return True
    return False


def _judge(
    slot: str, column: ExtractionColumn, data: dict[str, Any], text_collapsed: str
) -> KeptValue | DroppedValue | None:
    """Keep, drop (with a reason) or ignore (the model said null) one value."""
    raw, quote = data["value"], data["quote"]
    if raw is None:
        return None
    shown: str | float = raw if isinstance(raw, str) else float(raw)
    if isinstance(shown, str):
        shown = shown[:MAX_TEXT_VALUE]
    claimed_quote = quote[:MAX_QUOTE] if quote is not None else None

    def drop(reason: DropReason) -> DroppedValue:
        return DroppedValue(column.header, slot, reason, shown, claimed_quote)

    def keep(value: str | float, mark: Mark) -> KeptValue:
        text = _collapse(quote or "")
        if len(text) > MAX_QUOTE:
            text = text[: MAX_QUOTE - 1].rstrip() + "…"
        return KeptValue(column.header, slot, column.kind, value, mark, text)

    wanted = _collapse(quote) if quote is not None else ""
    if not wanted or wanted not in text_collapsed:
        return drop("quote_not_in_text")

    if column.kind == "number":
        number = float(raw)
        return (
            keep(number, "literal")
            if number_in_quote(number, wanted)
            else drop("number_not_in_quote")
        )

    if column.kind == "date":
        if not _ISO_DATE.fullmatch(raw):
            return drop("invalid_value")
        try:
            parsed = date.fromisoformat(raw)
        except ValueError:
            return drop("invalid_value")
        has_year, has_month, has_day = date_parts(wanted, parsed.day, parsed.month, parsed.year)
        if has_year and has_month and has_day:
            return keep(raw, "literal")
        if has_year or has_day:
            return keep(raw, "derived")
        return drop("date_not_in_quote")

    if column.kind == "text":
        value = _collapse(raw)
        if not value or len(value) > MAX_TEXT_VALUE:
            return drop("invalid_value")
        return keep(value, "literal" if value.casefold() in wanted.casefold() else "derived")

    allowed = ("Yes", "No") if column.kind == "yes_no" else column.options
    if raw not in allowed:
        return drop("invalid_value")
    return keep(raw, "derived")


def parse_reply(response: Any, task: ExtractionTask, text: str) -> ExtractionResult:
    """Turn a completed response into a result for ``text``, the text that was sent."""
    if _refused(response):
        return _failure("INVALID_RESPONSE", "The model refused to answer.", response)
    reply = getattr(response, "output_text", None)
    if not isinstance(reply, str) or not reply.strip():
        return _invalid("no text", response)
    try:
        data = json.loads(reply, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return _invalid("not JSON", response)
    if not isinstance(data, dict) or set(data) != {"applies", "values"}:
        return _invalid("the keys are not applies and values", response)
    applies, values = data["applies"], data["values"]
    slots = task.slots
    if not isinstance(applies, bool):
        return _invalid("applies is not true or false", response)
    if not isinstance(values, dict) or set(values) != {slot for slot, _ in slots}:
        return _invalid("the keys of values are not the requested slots", response)
    try:
        checked = {slot: _check_value_shape(slot, column, values[slot]) for slot, column in slots}
    except _Shape as exc:
        return _invalid(str(exc), response)

    input_tokens, output_tokens = tokens(response)
    if not applies:
        return ExtractionResult(
            OUTCOME_NOT_APPLICABLE, input_tokens=input_tokens, output_tokens=output_tokens
        )

    text_collapsed = _collapse(text)
    kept: list[KeptValue] = []
    dropped: list[DroppedValue] = []
    for slot, column in slots:
        judged = _judge(slot, column, checked[slot], text_collapsed)
        if isinstance(judged, KeptValue):
            kept.append(judged)
        elif isinstance(judged, DroppedValue):
            dropped.append(judged)
    return ExtractionResult(
        OUTCOME_FOUND if kept else OUTCOME_NOTHING_FOUND,
        kept=tuple(kept),
        dropped=tuple(dropped),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
