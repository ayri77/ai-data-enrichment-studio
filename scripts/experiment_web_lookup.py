"""Probe: can a model with web search fill one value per table row, with sources?

This is a measurement script, not part of the application. It reads a workbook, asks
the model once per row to find one value on the web, and records what came back. It
never writes into a workbook. See docs/31-universal-lookup-probe.md.

Run it as a module from the repository root:

    uv run --env-file .env python -m scripts.experiment_web_lookup \\
        --workbook samples/probe/mountains_probe.xlsx \\
        --question "Find the elevation of this mountain above sea level." \\
        --answer number --unit m --expected-column expected_height_m

Without --expected-column nothing is compared; the rows are only recorded.

Columns mode (one or more --target options) sends exactly the request the application's
web lookup will send: several columns per row, answered in one request, read by the real
reply reader. Existing cells in the target columns are only compared, never overwritten,
and nothing is ever written to the workbook:

    uv run --env-file .env python -m scripts.experiment_web_lookup \\
        --workbook samples/multi_column_tests/01_phones_multi_attributes.xlsx \\
        --context-columns Brand,Exact_Model --question "Fill the technical characteristics." \\
        --target Battery_mAh:number:mAh --target IP_Rating:text --target eSIM_Support:yes_no

Privacy: the cell values of each row are sent to the model provider, and the answers,
quotes and sources are written to artifacts/web-lookup-experiments/ (ignored by git).
Do not point the script at data that must not leave your machine. The API key is read
from OPENAI_API_KEY and is never written anywhere.

Two things are not confirmed by the official documentation (see the doc): that web
search and a JSON-schema answer (--structured json_schema) work in one request, and
what max_tool_calls does exactly. Both are switchable: --structured json_object|none
and --max-tool-calls 0 (the parameter is then not sent). The summary says which were used
and counts requests the API rejected.
"""

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from ai_data_enrichment.execution.models import FoundRecord
from ai_data_enrichment.providers.web_lookup import (
    PROMPT_VERSION,
    LookupColumn,
    LookupTask,
    WebLookupUsage,
    api_citations,
    claimed_not_reported,
    configuration_id,
    tokens,
)
from ai_data_enrichment.providers.web_lookup_handler import WebLookupHandler
from ai_data_enrichment.providers.web_lookup_reply import ParsedReply, ReplyFailure, SlotDiagnostic

# Prices read from https://developers.openai.com/api/docs/pricing on 2026-11-15
# (gpt-6-luna, standard tier; web search $10.00 per 1k calls; search content tokens are
# billed at model rates). They only feed the printed estimate.
DEFAULT_PRICE_PER_SEARCH = 0.01
DEFAULT_PRICE_INPUT_PER_MTOK = 0.10
DEFAULT_PRICE_OUTPUT_PER_MTOK = 0.50

DEFAULT_MAX_OUTPUT_TOKENS = 4096
DEFAULT_MAX_TOOL_CALLS = 3
INCLUDE_SOURCES = "web_search_call.action.sources"

INSTRUCTION = (
    "You find one value for one row of a table, using web search. "
    "Use the row's values to identify the object. "
    "Prefer official and reference sources. "
    "If the value cannot be established or the sources disagree, return null as the value "
    "and say why in the explanation. Never guess."
)


@dataclass(frozen=True)
class Settings:
    """What a request depends on besides the row."""

    question: str
    answer: str  # "number" or "text"
    unit: str | None
    model: str
    reasoning_effort: str | None
    search_context_size: str
    structured: str  # "json_schema", "json_object" or "none"
    max_tool_calls: int  # 0 = not sent
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    require_search: bool = True


@dataclass(frozen=True)
class Prices:
    per_search: float
    input_per_mtok: float
    output_per_mtok: float


# --- Request -----------------------------------------------------------------------


def answer_schema(answer: str) -> dict[str, Any]:
    """The requested shape, written for a strict JSON schema (all keys required)."""
    properties: dict[str, Any] = {
        "value": {"type": ["number" if answer == "number" else "string", "null"]},
        "sources": {"type": "array", "items": {"type": "string"}},
        "quote": {"type": ["string", "null"]},
        "explanation": {"type": "string"},
        "confident": {"type": "boolean"},
    }
    if answer == "number":
        properties["unit"] = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def instruction_text(settings: Settings) -> str:
    parts = [
        INSTRUCTION,
        "Fields: value; "
        + ("unit; " if settings.answer == "number" else "")
        + "sources (URLs the value was taken from); quote (a short verbatim excerpt from "
        "one source that contains the value, or null); explanation (one sentence); "
        "confident (true only if the sources agree).",
    ]
    if settings.structured != "json_schema":
        # Without a schema in the request the shape is only asked for in words.
        parts.append(
            "Answer with one JSON object matching this JSON Schema:\n"
            + json.dumps(answer_schema(settings.answer))
        )
    return "\n".join(parts)


def row_message(settings: Settings, context: dict[str, str]) -> str:
    if settings.answer == "number":
        kind = "a number" + (f" in {settings.unit}" if settings.unit else "")
    else:
        kind = "text"
    lines = [f"Question: {settings.question}", f"Answer as: {kind}", "Row:"]
    lines += [f"- {header}: {value}" for header, value in context.items()]
    return "\n".join(lines)


def build_request(settings: Settings, context: dict[str, str]) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": settings.model,
        "instructions": instruction_text(settings),
        "input": row_message(settings, context),
        "tools": [{"type": "web_search", "search_context_size": settings.search_context_size}],
        "tool_choice": "required" if settings.require_search else "auto",
        "include": [INCLUDE_SOURCES],
        "store": False,
        "max_output_tokens": settings.max_output_tokens,
    }
    if settings.reasoning_effort:
        request["reasoning"] = {"effort": settings.reasoning_effort}
    if settings.max_tool_calls > 0:
        request["max_tool_calls"] = settings.max_tool_calls
    if settings.structured == "json_schema":
        request["text"] = {
            "format": {
                "type": "json_schema",
                "name": "web_lookup_answer",
                "schema": answer_schema(settings.answer),
                "strict": True,
            }
        }
    elif settings.structured == "json_object":
        request["text"] = {"format": {"type": "json_object"}}
    return request


# --- Reply -------------------------------------------------------------------------


def parse_reply(text: str, settings: Settings) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse the model's text; return the answer and the reasons it does not fit the shape."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None, ["reply is not valid JSON"]
    if not isinstance(data, dict):
        return None, ["reply is not a JSON object"]

    problems: list[str] = []
    expected_keys = set(answer_schema(settings.answer)["properties"])
    for key in sorted(expected_keys - set(data)):
        problems.append(f"missing key: {key}")
    for key in sorted(set(data) - expected_keys):
        problems.append(f"unexpected key: {key}")

    value = data.get("value")
    if "value" in data and value is not None:
        if settings.answer == "number":
            if isinstance(value, bool) or not isinstance(value, int | float):
                problems.append("value is not a number")
            elif not math.isfinite(value):
                problems.append("value is not finite")
        elif not isinstance(value, str):
            problems.append("value is not text")
        elif not value.strip():
            problems.append("value is empty text")
    sources = data.get("sources")
    if "sources" in data and (
        not isinstance(sources, list) or not all(isinstance(s, str) for s in sources)
    ):
        problems.append("sources is not a list of strings")
    for key in ("quote", "unit"):
        if key in data and data[key] is not None and not isinstance(data[key], str):
            problems.append(f"{key} is not text or null")
    if "explanation" in data and not isinstance(data["explanation"], str):
        problems.append("explanation is not text")
    if "confident" in data and not isinstance(data["confident"], bool):
        problems.append("confident is not true or false")
    if (
        settings.answer == "number"
        and settings.unit
        and isinstance(value, int | float)
        and not isinstance(value, bool)
    ):
        unit = data.get("unit")
        if not isinstance(unit, str) or unit.strip().casefold() != settings.unit.strip().casefold():
            problems.append(f"unit is {unit!r}, asked for {settings.unit!r}")
    return (None if problems else data), problems


# --- Comparison and cost -------------------------------------------------------------


def is_empty(cell: object) -> bool:
    return cell is None or (isinstance(cell, str) and not cell.strip())


def to_number(cell: object) -> float | None:
    if isinstance(cell, bool):
        return None
    if isinstance(cell, int | float):
        return float(cell)
    try:
        return float(str(cell).strip().replace(",", "."))
    except ValueError:
        return None


def compare(expected: object, value: object, answer: str, tolerance_percent: float) -> str:
    """One of: match, mismatch, no_answer, correct_no_answer, false_answer, expected_unreadable."""
    if is_empty(expected):
        return "correct_no_answer" if value is None else "false_answer"
    if value is None:
        return "no_answer"
    if answer == "number":
        wanted = to_number(expected)
        if wanted is None:
            return "expected_unreadable"
        if isinstance(value, bool) or not isinstance(value, int | float):
            return "mismatch"
        allowed = abs(wanted) * tolerance_percent / 100
        return "match" if abs(float(value) - wanted) <= allowed else "mismatch"

    # Text: case and whitespace do not matter; a part of a text is not the text.
    def fold(text: object) -> str:
        return " ".join(str(text).split()).casefold()

    return "match" if fold(expected) == fold(value) else "mismatch"


def estimate_cost(searches: int, input_tokens: int, output_tokens: int, prices: Prices) -> float:
    return (
        searches * prices.per_search
        + input_tokens / 1_000_000 * prices.input_per_mtok
        + output_tokens / 1_000_000 * prices.output_per_mtok
    )


# --- One row -----------------------------------------------------------------------


def redact(text: str, secret: str | None) -> str:
    if secret:
        text = text.replace(secret, "[key]")
    return re.sub(r"sk-[A-Za-z0-9_\-*.]+", "[key]", text)[:300]


def run_row(
    client: Any,
    settings: Settings,
    row_number: int,
    context: dict[str, str],
    expected: object,
    has_expected: bool,
    tolerance_percent: float,
    api_key: str | None,
) -> dict[str, Any]:
    record: dict[str, Any] = {"row": row_number, "context": context}
    started = perf_counter()
    response: Any = None
    try:
        response = client.responses.create(**build_request(settings, context))
    except Exception as exc:
        record["status"] = "error"
        record["error_type"] = type(exc).__name__
        record["http_status"] = getattr(exc, "status_code", None)
        record["error_message"] = redact(str(exc), api_key)
    record["seconds"] = round(perf_counter() - started, 2)

    if response is not None:
        record["input_tokens"], record["output_tokens"] = tokens(response)
        record.update(api_citations(response))
        status = getattr(response, "status", None)
        text = getattr(response, "output_text", None)
        if status != "completed":
            record["status"] = "error"
            record["error_type"] = "IncompleteResponse"
            record["error_message"] = f"response status: {status}"
        elif not isinstance(text, str) or not text.strip():
            record["status"] = "shape_failure"
            record["shape_problems"] = ["reply has no text"]
        else:
            record["raw_text"] = text[:2000]
            data, problems = parse_reply(text, settings)
            if data is None:
                record["status"] = "shape_failure"
                record["shape_problems"] = problems
            else:
                record["status"] = "answered" if data["value"] is not None else "null"
                record["value"] = data["value"]
                record["unit"] = data.get("unit")
                record["claimed_sources"] = data["sources"]
                record["quote"] = data["quote"]
                record["explanation"] = data["explanation"]
                record["confident"] = data["confident"]
                reported = record["annotation_urls"] + record["source_urls"]
                record["claimed_not_reported"] = claimed_not_reported(data["sources"], reported)
        if record["status"] != "error":
            record["no_api_citation"] = not (record["annotation_urls"] or record["source_urls"])

    if has_expected:
        record["expected"] = None if is_empty(expected) else expected
        value = record.get("value") if record["status"] in ("answered", "null") else None
        record["comparison"] = compare(expected, value, settings.answer, tolerance_percent)
    return record


# --- Summary -----------------------------------------------------------------------


def summarize(
    records: list[dict[str, Any]], settings: Settings, prices: Prices, has_expected: bool
) -> dict[str, Any]:
    def count(status: str) -> int:
        return sum(r["status"] == status for r in records)

    replied = [r for r in records if r["status"] != "error"]
    searches = sum(r.get("search_calls", 0) for r in records)
    input_tokens = sum(r.get("input_tokens", 0) for r in records)
    output_tokens = sum(r.get("output_tokens", 0) for r in records)
    rejected = [r for r in records if r.get("http_status") == 400]
    totals: dict[str, Any] = {
        "rows": len(records),
        "answered": count("answered"),
        "nulls": count("null"),
        "shape_failures": count("shape_failure"),
        "transport_errors": count("error"),
        "rejected_by_api_400": len(rejected),
        "rows_without_api_citation": sum(bool(r.get("no_api_citation")) for r in replied),
        "rows_with_claimed_source_not_reported": sum(
            bool(r.get("claimed_not_reported")) for r in records
        ),
        "search_calls": searches,
        "search_calls_per_row": round(searches / len(records), 2) if records else None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "seconds_total": round(sum(r["seconds"] for r in records), 2),
        "estimated_cost": round(estimate_cost(searches, input_tokens, output_tokens, prices), 4),
    }
    if rejected:
        totals["first_rejection"] = rejected[0]["error_message"]
    if has_expected:
        comparisons = [r["comparison"] for r in records]
        with_truth = [r for r in records if r["comparison"] in ("match", "mismatch", "no_answer")]
        answered = [r for r in with_truth if r["comparison"] != "no_answer"]
        matches = sum(r["comparison"] == "match" for r in with_truth)
        totals["against_expected"] = {
            "rows_with_expected_value": len(with_truth),
            "match": matches,
            "mismatch": comparisons.count("mismatch"),
            "no_answer": comparisons.count("no_answer"),
            "coverage": round(len(answered) / len(with_truth), 3) if with_truth else None,
            "precision_when_answered": round(matches / len(answered), 3) if answered else None,
            "rows_with_empty_expected": comparisons.count("correct_no_answer")
            + comparisons.count("false_answer"),
            "correct_no_answer": comparisons.count("correct_no_answer"),
            "false_answer": comparisons.count("false_answer"),
            "expected_unreadable": comparisons.count("expected_unreadable"),
        }
    return {
        "settings": {
            "model": settings.model,
            "reasoning_effort": settings.reasoning_effort,
            "search_context_size": settings.search_context_size,
            "structured": settings.structured,
            "max_tool_calls_sent": settings.max_tool_calls or None,
            "tool_choice": "required" if settings.require_search else "auto",
            "answer": settings.answer,
            "unit": settings.unit,
            "question": settings.question,
        },
        "prices_used": {
            "per_search": prices.per_search,
            "input_per_mtok": prices.input_per_mtok,
            "output_per_mtok": prices.output_per_mtok,
        },
        "totals": totals,
    }


def print_summary(summary: dict[str, Any]) -> None:
    totals = summary["totals"]
    print(f"\nRows: {totals['rows']}")
    print(
        f"Answered: {totals['answered']} | null: {totals['nulls']} | "
        f"shape failures: {totals['shape_failures']} | "
        f"transport errors: {totals['transport_errors']} "
        f"(of which rejected with HTTP 400: {totals['rejected_by_api_400']})"
    )
    if "first_rejection" in totals:
        print(f"First rejection: {totals['first_rejection']}")
    print(
        f"Rows with no API-reported citation: {totals['rows_without_api_citation']} | "
        f"rows claiming a source the API did not report: "
        f"{totals['rows_with_claimed_source_not_reported']}"
    )
    expected = totals.get("against_expected")
    if expected:
        print(
            f"Against the expected column: match {expected['match']}, "
            f"mismatch {expected['mismatch']}, no answer {expected['no_answer']} "
            f"(of {expected['rows_with_expected_value']}) | "
            f"coverage {expected['coverage']} | "
            f"precision when answered {expected['precision_when_answered']}"
        )
        print(
            f"Rows with an empty expected cell: correct no answer "
            f"{expected['correct_no_answer']}, false answer {expected['false_answer']}"
        )
    prices = summary["prices_used"]
    print(
        f"Search calls: {totals['search_calls']} ({totals['search_calls_per_row']} per row) | "
        f"tokens in/out: {totals['input_tokens']}/{totals['output_tokens']} | "
        f"{totals['seconds_total']} s in total"
    )
    print(
        f"Estimated cost: {totals['estimated_cost']} USD at {prices['per_search']} per search, "
        f"{prices['input_per_mtok']}/{prices['output_per_mtok']} per 1M tokens in/out"
    )


# --- Workbook ----------------------------------------------------------------------


def read_workbook(
    path: Path, sheet: str | None, header_row: int, limit: int
) -> tuple[list[str], list[tuple[int, dict[str, object]]]]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook[sheet] if sheet else workbook.active
        if worksheet is None:
            raise ValueError("The workbook has no sheet")
        headers: list[str] = []
        rows: list[tuple[int, dict[str, object]]] = []
        for number, values in enumerate(worksheet.iter_rows(values_only=True), start=1):
            if number < header_row:
                continue
            if number == header_row:
                headers = [("" if v is None else str(v).strip()) for v in values]
                continue
            if all(is_empty(v) for v in values):
                continue
            rows.append((number, {h: v for h, v in zip(headers, values, strict=False) if h}))
            if len(rows) >= limit:
                break
        return [h for h in headers if h], rows
    finally:
        workbook.close()


def cell_text(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def row_context(row: dict[str, object], columns: list[str]) -> dict[str, str]:
    return {c: cell_text(row[c]) for c in columns if c in row and not is_empty(row[c])}


# --- Columns mode: the request the application will send -----------------------------

KINDS = ("text", "number", "yes_no")
MAX_TARGETS = 20
# An error code after which every later row would fail the same way.
STOP_CODES = ("SOURCE_ACCESS_DENIED", "REQUEST_REFUSED")
YES_WORDS = frozenset({"yes", "true", "y", "1", "да", "так", "ja"})
NO_WORDS = frozenset({"no", "false", "n", "0", "ні", "нет", "nein"})
# Options of the single-value mode that the application's request fixes; they must stay at
# their defaults in columns mode, because they would change what is sent.
FIXED_IN_COLUMNS_MODE = ("structured", "max_tool_calls", "max_output_tokens", "search")
FIXED_OPTION_NAMES = {
    "structured": "--structured",
    "max_tool_calls": "--max-tool-calls",
    "max_output_tokens": "--max-output-tokens",
    "search": "--search",
    "search_context_size": "--search-context-size",
}


def parse_target(spec: str) -> tuple[str, str, str | None]:
    """Read "Header:kind[:unit]"; split from the right, so only the header may hold text
    that looks like part of the spec. Raises ValueError with a clear message."""
    parts = spec.split(":")
    if len(parts) >= 2 and parts[-1].strip() in KINDS:
        header, kind, unit = ":".join(parts[:-1]), parts[-1].strip(), None
    elif len(parts) >= 3 and parts[-2].strip() in KINDS:
        header, kind, unit = ":".join(parts[:-2]), parts[-2].strip(), parts[-1].strip()
        if kind != "number":
            raise ValueError(f"--target {spec!r}: a unit is only allowed with the kind number")
        if not unit:
            raise ValueError(f"--target {spec!r}: the unit is empty")
    else:
        raise ValueError(
            f"--target {spec!r}: expected Header:kind or Header:number:unit with kind one of "
            f"{', '.join(KINDS)}"
        )
    header = header.strip()
    if not header:
        raise ValueError(f"--target {spec!r}: the header is empty")
    return header, kind, unit


def parse_yes_no(cell: object) -> bool | None:
    """A cell read as yes or no; None when it is neither."""
    if isinstance(cell, bool):
        return cell
    word = " ".join(cell_text(cell).split()).casefold()
    if word in YES_WORDS:
        return True
    if word in NO_WORDS:
        return False
    return None


def compare_existing(
    kind: str, existing: object, found: object, tolerance_percent: float
) -> str | None:
    """agree, differ, not_comparable, nothing_found; None when the cell held no value."""
    if is_empty(existing):
        return None
    if found is None:
        return "nothing_found"
    if kind == "yes_no":
        wanted = parse_yes_no(existing)
        if wanted is None:
            return "not_comparable"
        return "agree" if wanted == (found == "Yes") else "differ"
    if kind == "number":
        outcome = compare(existing, found, "number", tolerance_percent)
        return {"match": "agree", "mismatch": "differ"}.get(outcome, "not_comparable")
    outcome = compare(cell_text(existing), found, "text", tolerance_percent)
    return "agree" if outcome == "match" else "differ"


def json_cell(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return str(value)


def context_input(value: object) -> str | float | None:
    """A context cell as the handler takes it: text as text, a number as a float."""
    if is_empty(value):
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        return float(value)
    return value if isinstance(value, str) else str(value)


class _Refused(Exception):
    """Raised by the dry-run capture; carries no status code, so it is never an HTTP error."""


class _Responses:
    def __init__(self, create: Any) -> None:
        self.create = create


class RecordingClient:
    """Wraps the API object for columns mode only: when a request fails with an HTTP
    status, remember the exception's own message (redacted) and print it once."""

    def __init__(self, inner: Any, api_key: str | None) -> None:
        self._inner = inner
        self._key = api_key
        self.last_http_message: str | None = None
        self.printed = False
        self.responses = _Responses(self._create)

    def _create(self, **kwargs: Any) -> Any:
        try:
            return self._inner.responses.create(**kwargs)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if isinstance(status, int) and not isinstance(status, bool):
                self.last_http_message = f"HTTP {status}: {redact(str(exc), self._key)}"
                if not self.printed:
                    self.printed = True
                    print(f"API error text, shown once: {self.last_http_message}", flush=True)
            raise


class _Capture:
    """Stands in for the API in --dry-run: keeps the keyword arguments, sends nothing."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None
        self.responses = _Responses(self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        raise _Refused


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def slot_entry(
    column: LookupColumn,
    record: FoundRecord | None,
    diagnostic: SlotDiagnostic | None,
    existing: object,
    tolerance_percent: float,
) -> dict[str, Any]:
    found = record.slots.get(column.slot) if record is not None else None
    entry: dict[str, Any] = {
        "value": found.value if found else None,
        "basis": found.basis if found else None,
        "links": list(found.sources) if found else [],
        "quote": found.quote if found else None,
        "unit": found.unit if found else None,
        "diagnostic": asdict(diagnostic) if diagnostic else None,
    }
    if not is_empty(existing):
        entry["existing"] = json_cell(existing)
        comparison = (
            compare_existing(column.kind, existing, entry["value"], tolerance_percent)
            if diagnostic is not None
            else None
        )
        entry["comparison"] = comparison
        entry["agrees"] = {"agree": True, "differ": False}.get(comparison or "")
    return entry


def show_value(column: LookupColumn, entry: dict[str, Any]) -> str:
    diagnostic = entry["diagnostic"]
    if entry["value"] is None:
        reason = diagnostic["reason"] if diagnostic else "no reply"
        return f"{column.header}: — ({reason})"
    value = entry["value"]
    shown = cell_text(value) if isinstance(value, float) else str(value)
    text = f"{column.header}: {shown} ({entry['basis']}, {plural(len(entry['links']), 'link')})"
    note = {
        "agree": "agrees with the cell",
        "differ": "differs from the cell",
        "not_comparable": "cell not comparable",
    }.get(entry.get("comparison") or "")
    return f"{text} [{note}]" if note else text


def run_columns_row(
    handler: WebLookupHandler,
    usage: WebLookupUsage,
    task: LookupTask,
    task_json: str,
    number: int,
    values: dict[str, object],
    tolerance_percent: float,
    replies: list[ParsedReply | ReplyFailure],
    client: RecordingClient,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {"task": task_json}
    sent: dict[str, object] = {}
    for slot, header in task.context:
        value = context_input(values.get(header))
        if value is not None:
            inputs[slot] = value
            sent[header] = value
    replies.clear()
    client.last_http_message = None
    before = usage.totals()
    started = perf_counter()
    result = handler.execute(inputs=inputs, parameters={})
    seconds = round(perf_counter() - started, 2)
    after = usage.totals()

    record_value = result.value if isinstance(result.value, FoundRecord) else None
    reply = replies[0] if replies else None
    diagnostics = {d.slot: d for d in reply.diagnostics} if reply is not None else {}
    code = "ok" if result.status == "success" else (result.error_code or "ERROR")
    row: dict[str, Any] = {
        "row": number,
        "context": sent,
        "status": code,
        "message": result.message,
        "seconds": seconds,
        "requests": after.requests - before.requests,
        "input_tokens": after.input_tokens - before.input_tokens,
        "output_tokens": after.output_tokens - before.output_tokens,
        "searches": after.web_searches - before.web_searches,
        "targets": {},
    }
    if client.last_http_message:
        row["http_error"] = client.last_http_message
    for column in task.columns:
        row["targets"][column.header] = slot_entry(
            column,
            record_value,
            diagnostics.get(column.slot),
            values.get(column.header),
            tolerance_percent,
        )
    return row


def print_columns_row(task: LookupTask, row: dict[str, Any]) -> None:
    parts = [f"[row {row['row']}] {row['status']}"]
    if row["status"] != "ok" and not any(e["diagnostic"] for e in row["targets"].values()):
        parts.append(str(row["message"]))
    else:
        parts += [show_value(c, row["targets"][c.header]) for c in task.columns]
    parts += [f"searches={row['searches']}", f"{row['seconds']} s"]
    print(" | ".join(parts), flush=True)


def summarize_columns(
    rows: list[dict[str, Any]],
    task: LookupTask,
    task_json: str,
    usage: WebLookupUsage,
    prices: Prices,
    args: argparse.Namespace,
    planned: int,
    model: str,
    effort: str | None,
) -> dict[str, Any]:
    entries = [
        (entry, row) for row in rows for entry in row["targets"].values() if entry["diagnostic"]
    ]
    found = [e for e, _ in entries if e["diagnostic"]["found"]]
    reasons = Counter(e["diagnostic"]["reason"] for e, _ in entries if not e["diagnostic"]["found"])
    levels = Counter(m for e in found for m in e["diagnostic"]["link_matches"])
    comparisons = Counter(e.get("comparison") for e, _ in entries if "existing" in e)
    totals = usage.totals()
    cost = estimate_cost(totals.web_searches, totals.input_tokens, totals.output_tokens, prices)
    seconds = round(sum(r["seconds"] for r in rows), 2)
    asked = len(rows)
    return {
        "mode": "columns",
        "workbook": str(args.workbook),
        "settings": {
            "model": model,
            "reasoning_effort": effort,
            "prompt_version": PROMPT_VERSION,
            "configuration_id": configuration_id(model, effort),
            "question": task.question,
            "context_columns": [header for _, header in task.context],
            "targets": [{"header": c.header, "kind": c.kind, "unit": c.unit} for c in task.columns],
            "tolerance_percent": args.tolerance,
            "timeout_seconds": args.timeout,
        },
        "task_json": task_json,
        "rows": {
            "planned": planned,
            "asked": asked,
            "succeeded": sum(r["status"] == "ok" for r in rows),
            "failed_by_code": dict(Counter(r["status"] for r in rows if r["status"] != "ok")),
            "stopped_early": rows[-1]["status"]
            if rows and rows[-1]["status"] in STOP_CODES
            else None,
        },
        "slots": {
            "found": len(found),
            "not_found": len(entries) - len(found),
            "not_found_by_reason": dict(reasons),
        },
        "found_by_basis": {
            "stated": sum(e["basis"] == "stated" for e in found),
            "concluded": sum(e["basis"] == "concluded" for e in found),
        },
        "links": {
            "kept_exact": levels["exact"],
            "kept_address": levels["address"],
            "dropped_not_reported_by_search": sum(
                e["diagnostic"]["links_dropped"] for e, _ in entries
            ),
            "quotes_truncated": sum(e["diagnostic"]["quote_truncated"] for e in found),
        },
        "comparison_with_existing": {
            "compared": comparisons["agree"] + comparisons["differ"],
            "agree": comparisons["agree"],
            "differ": comparisons["differ"],
            "not_comparable": comparisons["not_comparable"],
            "existing_but_nothing_found": comparisons["nothing_found"],
        },
        "usage": {
            "requests": totals.requests,
            "input_tokens": totals.input_tokens,
            "output_tokens": totals.output_tokens,
            "web_searches": totals.web_searches,
        },
        "seconds_total": seconds,
        "seconds_per_row": round(seconds / asked, 2) if asked else None,
        "prices_used": {
            "per_search": prices.per_search,
            "input_per_mtok": prices.input_per_mtok,
            "output_per_mtok": prices.output_per_mtok,
        },
        "estimated_cost": {
            "total": round(cost, 4),
            "per_row": round(cost / asked, 4) if asked else None,
            "per_found_value": round(cost / len(found), 4) if found else None,
        },
    }


def print_columns_summary(summary: dict[str, Any]) -> None:
    settings, rows, slots = summary["settings"], summary["rows"], summary["slots"]
    links, compared, usage = summary["links"], summary["comparison_with_existing"], summary["usage"]
    cost = summary["estimated_cost"]
    print(
        f"\nConfiguration: {settings['configuration_id']} "
        f"(prompt version {settings['prompt_version']})"
    )
    print(
        f"Rows: asked {rows['asked']} of {rows['planned']} | succeeded {rows['succeeded']} | "
        f"failed {rows['failed_by_code'] or 0}"
    )
    print(
        f"Values: found {slots['found']} | not found {slots['not_found']} "
        f"{slots['not_found_by_reason'] or ''}".rstrip()
    )
    basis = summary["found_by_basis"]
    print(f"Found by basis: stated {basis['stated']}, concluded {basis['concluded']}")
    print(
        f"Links: kept {links['kept_exact']} exact + {links['kept_address']} by address | "
        f"dropped (not reported by the search) {links['dropped_not_reported_by_search']} | "
        f"quotes truncated {links['quotes_truncated']}"
    )
    print(
        f"Against existing cells: compared {compared['compared']} (agree {compared['agree']}, "
        f"differ {compared['differ']}) | not comparable {compared['not_comparable']} | "
        f"existing value but nothing found {compared['existing_but_nothing_found']}"
    )
    print(
        f"Usage: {usage['requests']} requests | tokens in/out {usage['input_tokens']}/"
        f"{usage['output_tokens']} | web searches {usage['web_searches']} | "
        f"{summary['seconds_total']} s in total, {summary['seconds_per_row']} s per row"
    )
    prices = summary["prices_used"]
    print(
        f"Estimated cost: {cost['total']} USD in total, {cost['per_row']} per row, "
        f"{cost['per_found_value']} per found value (at {prices['per_search']} per search, "
        f"{prices['input_per_mtok']}/{prices['output_per_mtok']} per 1M tokens in/out)"
    )
    print("This is one run, one observation: the same request can answer differently next time.")
    print("The cost is an estimate from the --price-* options; it was not compared with the bill.")


def check_columns_options(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    mixed = [
        flag
        for flag, value in (
            ("--answer", args.answer),
            ("--unit", args.unit),
            ("--expected-column", args.expected_column),
        )
        if value is not None
    ]
    if mixed:
        parser.error(
            f"--target (columns mode) cannot be combined with {', '.join(mixed)}; those belong to "
            "the single-value mode"
        )
    fixed = [
        flag
        for name, flag in FIXED_OPTION_NAMES.items()
        if getattr(args, name) != parser.get_default(name)
    ]
    if fixed:
        parser.error(
            f"{', '.join(fixed)} cannot be changed in columns mode: the application's request "
            "fixes them, and the probe sends exactly that request"
        )
    if not args.context_columns:
        parser.error("--context-columns is required with --target")
    if not (1 <= len(args.target) <= MAX_TARGETS):
        parser.error(f"Give between 1 and {MAX_TARGETS} --target options")


def build_columns_task(
    parser: argparse.ArgumentParser, args: argparse.Namespace, headers: list[str]
) -> LookupTask:
    context = [c.strip() for c in args.context_columns.split(",") if c.strip()]
    if not context:
        parser.error("--context-columns is empty")
    unknown = [c for c in context if c not in headers]
    if unknown:
        parser.error(f"Unknown --context-columns: {unknown}; headers are {headers}")
    columns: list[LookupColumn] = []
    for index, spec in enumerate(args.target, start=1):
        try:
            header, kind, unit = parse_target(spec)
        except ValueError as exc:
            parser.error(str(exc))
        if header not in headers:
            parser.error(f"--target header {header!r} is not a header of the sheet: {headers}")
        if header in context:
            parser.error(f"--target {header!r} is also a context column")
        columns.append(
            LookupColumn(slot=f"value_{index}", header=header, kind=kind, unit=unit)  # type: ignore[arg-type]
        )
    try:
        return LookupTask(
            question=args.question,
            context=tuple((f"ctx_{i}", h) for i, h in enumerate(context, start=1)),
            columns=tuple(columns),
        )
    except ValidationError as exc:
        parser.error(f"The lookup task is not valid: {exc.errors()[0]['msg']}")


def run_columns(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    headers: list[str],
    rows: list[tuple[int, dict[str, object]]],
    client: Any,
) -> int:
    task = build_columns_task(parser, args, headers)
    task_json = task.to_json()
    model = args.model
    effort = args.reasoning_effort or None
    replies: list[ParsedReply | ReplyFailure] = []

    def make_handler(api: Any, usage: WebLookupUsage) -> WebLookupHandler:
        return WebLookupHandler(
            api,
            model=model,
            reasoning_effort=effort,
            usage=usage,
            timeout_seconds=args.timeout,
            on_reply=lambda parsed: replies.append(parsed),
        )

    if args.dry_run:
        # The handler builds the request; a stand-in for the API keeps it and sends nothing.
        capture = _Capture()
        handler = make_handler(capture, WebLookupUsage())
        number, values = rows[0]
        inputs: dict[str, Any] = {"task": task_json}
        for slot, header in task.context:
            value = context_input(values.get(header))
            if value is not None:
                inputs[slot] = value
        outcome = handler.execute(inputs=inputs, parameters={})
        print(f"First of {len(rows)} rows (sheet row {number}); nothing is sent.")
        if capture.kwargs is None:
            print(f"This row would not be sent: {outcome.error_code}: {outcome.message}")
        else:
            print(json.dumps(capture.kwargs, ensure_ascii=False, indent=2))
        return 0

    api_key = os.getenv("OPENAI_API_KEY")
    if client is None:
        if not api_key:
            parser.error("Set OPENAI_API_KEY in the local shell or an ignored .env file")
        from openai import OpenAI

        client = OpenAI(timeout=args.timeout, max_retries=0)
    if args.concurrency > 1:
        print("Note: --concurrency is ignored in columns mode; rows are asked one at a time.")

    usage = WebLookupUsage()
    recording = RecordingClient(client, api_key)
    handler = make_handler(recording, usage)
    prices = Prices(args.price_per_search, args.price_input_per_mtok, args.price_output_per_mtok)
    records: list[dict[str, Any]] = []
    for number, values in rows:
        row = run_columns_row(
            handler, usage, task, task_json, number, values, args.tolerance, replies, recording
        )
        records.append(row)
        print_columns_row(task, row)
        if row["status"] in STOP_CODES:
            print(f"Stopped after row {number}: {row['status']}: {row['message']}")
            if row["status"] == "REQUEST_REFUSED":
                print(
                    "The API did not accept the request shape. Run with --dry-run to see exactly "
                    "what is sent."
                )
            else:
                print("Check the key and its access to the model and to web search.")
            break

    directory = args.out / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "rows.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    summary = summarize_columns(
        records, task, task_json, usage, prices, args, len(rows), model, effort
    )
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print_columns_summary(summary)
    print(f"Report: {(directory / 'summary.json').resolve()}")
    print("Row contents, answers and sources are in rows.jsonl there; do not commit them.")
    return 0


# --- Command line ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--sheet", help="Sheet name (default: the active sheet)")
    parser.add_argument("--rows", type=int, default=20, help="Rows from the top (default 20)")
    parser.add_argument("--header-row", type=int, default=1)
    parser.add_argument("--question", required=True, help="What to find for each row")
    parser.add_argument(
        "--context-columns",
        help="Comma-separated headers sent to the model (default: all but the expected column)",
    )
    parser.add_argument("--answer", choices=("number", "text"), default=None)
    parser.add_argument("--unit", help="Unit of a number answer, e.g. m")
    parser.add_argument("--expected-column", help="Header of a column with the right answers")
    parser.add_argument(
        "--target",
        action="append",
        metavar="HEADER:KIND[:UNIT]",
        help="Columns mode (1 to 20, repeatable): a column to fill; kind is text, number or "
        "yes_no, a unit only with number, e.g. Battery_mAh:number:mAh. The probe splits on ':' "
        "from the right, so a header that contains a colon is not supported. Sends exactly the "
        "application's request (the request options --structured, --max-tool-calls, "
        "--max-output-tokens, --search and --search-context-size must stay at their defaults), "
        "needs --context-columns, cannot be combined with --answer, --unit or "
        "--expected-column, never skips a cell that holds a value (it is only compared) and "
        "never writes into the workbook",
    )
    parser.add_argument("--tolerance", type=float, default=10.0, help="Percent, for numbers")
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--search-context-size", choices=("low", "medium", "high"), default="low")
    parser.add_argument(
        "--structured",
        choices=("json_schema", "json_object", "none"),
        default="json_schema",
        help="How the answer shape is requested; json_schema together with web search is "
        "not confirmed by the documentation",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=DEFAULT_MAX_TOOL_CALLS,
        help="Cap on search calls per request; 0 does not send the parameter",
    )
    parser.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    parser.add_argument(
        "--search",
        choices=("required", "auto"),
        default="required",
        help="required: the model must search; auto: it may answer without searching",
    )
    parser.add_argument("--timeout", type=float, default=90.0, help="Seconds per request")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Parallel requests (ignored in columns mode: rows are asked one at a time)",
    )
    parser.add_argument("--price-per-search", type=float, default=DEFAULT_PRICE_PER_SEARCH)
    parser.add_argument("--price-input-per-mtok", type=float, default=DEFAULT_PRICE_INPUT_PER_MTOK)
    parser.add_argument(
        "--price-output-per-mtok", type=float, default=DEFAULT_PRICE_OUTPUT_PER_MTOK
    )
    parser.add_argument("--out", type=Path, default=Path("artifacts/web-lookup-experiments"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request for the first row (in columns mode: the exact keyword arguments "
        "of responses.create) and send nothing",
    )
    return parser


def run(argv: Sequence[str] | None = None, client: Any = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.target:
        check_columns_options(parser, args)
    args.answer = args.answer or "text"
    if args.rows < 1 or args.header_row < 1 or args.concurrency < 1:
        parser.error("--rows, --header-row and --concurrency must be positive")
    if args.max_tool_calls < 0 or args.max_output_tokens < 1 or args.timeout <= 0:
        parser.error("--max-tool-calls, --max-output-tokens and --timeout are out of range")
    if args.unit and args.answer != "number" and not args.target:
        parser.error("--unit only applies with --answer number")
    if not args.question.strip():
        parser.error("--question cannot be empty")
    if not args.workbook.is_file():
        parser.error(f"Workbook not found: {args.workbook}")

    try:
        headers, rows = read_workbook(args.workbook, args.sheet, args.header_row, args.rows)
    except (KeyError, ValueError, OSError) as exc:
        parser.error(f"Cannot read the workbook: {type(exc).__name__}: {exc}")
    if not rows:
        parser.error("No data rows found below the header row")
    if args.target:
        return run_columns(parser, args, headers, rows, client)
    if args.expected_column and args.expected_column not in headers:
        parser.error(f"--expected-column {args.expected_column!r} is not a header: {headers}")
    if args.context_columns:
        columns = [c.strip() for c in args.context_columns.split(",") if c.strip()]
        unknown = [c for c in columns if c not in headers]
        if unknown:
            parser.error(f"Unknown --context-columns: {unknown}; headers are {headers}")
        if args.expected_column in columns:
            parser.error("The expected column must not be among --context-columns")
    else:
        columns = [h for h in headers if h != args.expected_column]

    settings = Settings(
        question=args.question.strip(),
        answer=args.answer,
        unit=args.unit,
        model=args.model,
        reasoning_effort=args.reasoning_effort or None,
        search_context_size=args.search_context_size,
        structured=args.structured,
        max_tool_calls=args.max_tool_calls,
        max_output_tokens=args.max_output_tokens,
        require_search=args.search == "required",
    )
    prices = Prices(args.price_per_search, args.price_input_per_mtok, args.price_output_per_mtok)
    contexts = [row_context(values, columns) for _, values in rows]

    if args.dry_run:
        print(f"First of {len(rows)} rows (sheet row {rows[0][0]}); nothing is sent.")
        print(json.dumps(build_request(settings, contexts[0]), ensure_ascii=False, indent=2))
        return 0

    api_key = os.getenv("OPENAI_API_KEY")
    if client is None:
        if not api_key:
            parser.error("Set OPENAI_API_KEY in the local shell or an ignored .env file")
        from openai import OpenAI

        client = OpenAI(timeout=args.timeout, max_retries=0)

    has_expected = bool(args.expected_column)

    def work(index: int) -> dict[str, Any]:
        number, values = rows[index]
        record = run_row(
            client,
            settings,
            number,
            contexts[index],
            values.get(args.expected_column) if args.expected_column else None,
            has_expected,
            args.tolerance,
            api_key,
        )
        shown = record.get("value", record.get("error_type", record.get("shape_problems")))
        print(
            f"[row {number}] {record['status']} | {shown} | "
            f"searches={record.get('search_calls', '-')} | {record['seconds']} s"
            + (f" | {record['comparison']}" if has_expected else ""),
            flush=True,
        )
        return record

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        records = list(pool.map(work, range(len(rows))))

    directory = args.out / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "rows.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    summary = summarize(records, settings, prices, has_expected)
    summary["workbook"] = str(args.workbook)
    summary["tolerance_percent"] = args.tolerance if has_expected else None
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print_summary(summary)
    print(f"Report: {(directory / 'summary.json').resolve()}")
    print("Row contents, answers and sources are in rows.jsonl there; do not commit them.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
