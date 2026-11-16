"""Read a web lookup reply strictly: a claim is kept only where it can be traced.

The reply must be JSON of exactly the requested shape, or the whole row fails. Within
a well-formed reply each slot is judged on its own: a value is kept only with a
basis, in the unit asked for, and with at least one link the search itself reported.
Anything else becomes "not found" for that slot, with a reason (never the content).
Nothing is converted, repaired or guessed.
"""

import json
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from ai_data_enrichment.execution.models import Evidence, FoundRecord, FoundValue
from ai_data_enrichment.providers.web_lookup import (
    LOOKUP_SOURCE,
    MAX_LINK,
    MAX_LINKS,
    LookupColumn,
    LookupTask,
    api_citations,
    clean_text,
    normalize_url,
    strip_tracking,
    without_query,
)

MAX_TEXT_VALUE = 500
MAX_QUOTE = 300
MAX_DROPPED_TEXT = 300

LinkMatch = Literal["exact", "address"]
NotFoundReason = Literal[
    "no_value",
    "blank_value",
    "value_too_long",
    "unit_mismatch",
    "missing_basis",
    "no_reported_link",
]
DropReason = Literal["not_reported", "not_a_link", "duplicate", "over_limit"]


@dataclass(frozen=True)
class SlotDiagnostic:
    """What became of one slot.

    It holds no cell values. ``dropped`` does hold the links the model named but the
    reader did not keep (cleaned, cut to 300 characters), each with the one reason.
    """

    slot: str
    found: bool
    reason: NotFoundReason | None = None
    # For each kept link, how it matched a reported one: "exact" after the usual
    # normalisation, or "address" when the claimed link had no query string and
    # the reported one had.
    link_matches: tuple[LinkMatch, ...] = ()
    links_dropped: int = 0
    dropped: tuple[tuple[str, DropReason], ...] = ()
    quote_truncated: bool = False


@dataclass(frozen=True)
class ParsedReply:
    record: FoundRecord
    diagnostics: tuple[SlotDiagnostic, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class ReplyFailure:
    """The whole row failed: ``code`` is an operation error code."""

    code: str
    message: str
    diagnostics: tuple[SlotDiagnostic, ...] = ()


class _Shape(Exception):
    pass


def _reject_constant(name: str) -> Any:
    raise ValueError(name)


def _is_text_or_none(value: object) -> bool:
    return value is None or isinstance(value, str)


def _check_slot_shape(slot: str, column: LookupColumn, data: object) -> dict[str, Any]:
    expected = ["value", "basis", "sources", "quote"]
    if column.unit is not None:
        expected.insert(1, "unit")
    if not isinstance(data, dict) or set(data) != set(expected):
        raise _Shape(f"{slot}: not an object with the requested keys")
    value = data["value"]
    if value is not None:
        if column.kind == "text":
            ok = isinstance(value, str)
        elif column.kind == "yes_no":
            ok = isinstance(value, bool)
        else:
            ok = isinstance(value, int | float) and not isinstance(value, bool)
            if ok:
                try:
                    ok = math.isfinite(float(value))
                except OverflowError:
                    ok = False
        if not ok:
            raise _Shape(f"{slot}: value has the wrong type")
    if data["basis"] not in (None, "stated", "concluded"):
        raise _Shape(f"{slot}: basis is not stated or concluded")
    sources = data["sources"]
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        raise _Shape(f"{slot}: sources is not a list of text")
    if not _is_text_or_none(data["quote"]) or not _is_text_or_none(data.get("unit")):
        raise _Shape(f"{slot}: quote or unit is not text")
    return data


def _is_web_link(url: str) -> bool:
    if not url or len(url) > MAX_LINK or clean_text(url) != url or " " in url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


class _Reported:
    """The links the API reported for one response, indexed for matching."""

    def __init__(self, urls: list[str]) -> None:
        self.exact: dict[str, str] = {}
        self.address: dict[str, str] = {}
        for url in urls:
            if not _is_web_link(url):
                continue
            key = normalize_url(url)
            self.exact.setdefault(key, url)
            self.address.setdefault(without_query(key), url)

    def match(self, claimed: str) -> tuple[str, LinkMatch] | None:
        """The reported link a claimed (cleaned, well-formed) one stands for, and how."""
        key = normalize_url(claimed)
        if key in self.exact:
            return self.exact[key], "exact"
        # The weaker level covers one observed case only: the model names a page
        # without a parameter the search reported it with. A claimed link that has its
        # own query string must match exactly, because a query can select a different
        # variant of the page (``?sku=...``).
        if urlsplit(key).query:
            return None
        weak = self.address.get(key)
        return (weak, "address") if weak is not None else None


def _resolve_links(
    claimed: list[str], reported: _Reported
) -> tuple[list[str], list[LinkMatch], list[tuple[str, DropReason]]]:
    """Keep up to MAX_LINKS reported links; every other claimed link gets one reason.

    Checks in this order: not a web link, not reported, already kept (a duplicate),
    more than MAX_LINKS. A kept link is the reported one without tracking parameters.
    """
    kept: list[str] = []
    levels: list[LinkMatch] = []
    dropped: list[tuple[str, DropReason]] = []
    for raw in claimed:
        text = clean_text(raw)
        shown = text[:MAX_DROPPED_TEXT]
        if not _is_web_link(text):
            dropped.append((shown, "not_a_link"))
            continue
        found = reported.match(text)
        if found is None:
            dropped.append((shown, "not_reported"))
            continue
        link = strip_tracking(found[0])
        if link in kept:
            dropped.append((shown, "duplicate"))
        elif len(kept) >= MAX_LINKS:
            dropped.append((shown, "over_limit"))
        else:
            kept.append(link)
            levels.append(found[1])
    return kept, levels, dropped


def _slot(
    column: LookupColumn, data: dict[str, Any], reported: _Reported
) -> tuple[FoundValue, SlotDiagnostic]:
    slot = column.slot
    missing = FoundValue(value=None, basis=None)

    def fail(reason: NotFoundReason, **extra: Any) -> tuple[FoundValue, SlotDiagnostic]:
        return missing, SlotDiagnostic(slot, False, reason, **extra)

    raw = data["value"]
    if raw is None:
        return fail("no_value")
    value: str | float
    if column.kind == "yes_no":
        value = "Yes" if raw else "No"
    elif column.kind == "number":
        value = float(raw)
    else:
        value = clean_text(raw)
        if not value:
            return fail("blank_value")
        if len(value) > MAX_TEXT_VALUE:
            return fail("value_too_long")

    if column.kind == "number" and column.unit is not None:
        given = data.get("unit")
        if not isinstance(given, str) or clean_text(given).casefold() != column.unit.casefold():
            return fail("unit_mismatch")

    if data["basis"] is None:
        return fail("missing_basis")

    links, levels, dropped = _resolve_links(data["sources"], reported)
    if not links:
        return fail("no_reported_link", links_dropped=len(dropped), dropped=tuple(dropped))

    quote = clean_text(data["quote"]) if data["quote"] is not None else ""
    truncated = len(quote) > MAX_QUOTE
    if truncated:
        quote = quote[: MAX_QUOTE - 1].rstrip() + "…"
    found = FoundValue(
        value=value,
        basis=data["basis"],
        sources=tuple(links),
        quote=quote or None,
        unit=column.unit if column.kind == "number" else None,
    )
    return found, SlotDiagnostic(
        slot,
        True,
        link_matches=tuple(levels),
        links_dropped=len(dropped),
        dropped=tuple(dropped),
        quote_truncated=truncated,
    )


def _shape_failure(detail: str) -> ReplyFailure:
    return ReplyFailure(
        "INVALID_RESPONSE", f"The lookup reply does not have the requested shape: {detail}."
    )


def parse_reply(response: Any, task: LookupTask) -> ParsedReply | ReplyFailure:
    """Turn a completed response into a record, or say why the row failed."""
    text = getattr(response, "output_text", None)
    if not isinstance(text, str) or not text.strip():
        return _shape_failure("no text")
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return _shape_failure("not JSON")
    expected = [column.slot for column in task.columns]
    if not isinstance(data, dict) or set(data) != set(expected):
        return _shape_failure("the keys are not the requested slots")
    try:
        checked = {c.slot: _check_slot_shape(c.slot, c, data[c.slot]) for c in task.columns}
    except _Shape as exc:
        return _shape_failure(str(exc))

    cited = api_citations(response)
    reported = _Reported([*cited["source_urls"], *cited["annotation_urls"]])

    values: dict[str, FoundValue] = {}
    diagnostics: list[SlotDiagnostic] = []
    for column in task.columns:
        found, diagnostic = _slot(column, checked[column.slot], reported)
        values[column.slot] = found
        diagnostics.append(diagnostic)

    if all(item.value is None for item in values.values()):
        reasons = Counter(d.reason for d in diagnostics)
        detail = ", ".join(f"{reason}: {count}" for reason, count in sorted(reasons.items()))
        return ReplyFailure(
            "NOTHING_FOUND",
            f"The web search found no traceable value for any column ({detail}).",
            tuple(diagnostics),
        )

    links = dict.fromkeys(link for item in values.values() for link in item.sources)
    return ParsedReply(
        record=FoundRecord(slots=values),
        diagnostics=tuple(diagnostics),
        evidence=tuple(Evidence(source=LOOKUP_SOURCE, reference=link) for link in links),
    )
