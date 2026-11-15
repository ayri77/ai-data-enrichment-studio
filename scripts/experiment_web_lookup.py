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
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

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


def normalize_url(url: str) -> str:
    """Compare links without the parts that do not change the page."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    )
    return urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/"), query, ""))


def api_citations(response: Any) -> dict[str, Any]:
    """What the API itself reported: url_citation annotations, search sources, search calls."""
    annotations: list[str] = []
    sources: list[str] = []
    searches = 0
    for item in getattr(response, "output", None) or []:
        kind = getattr(item, "type", None)
        if kind == "web_search_call":
            searches += 1
            for source in getattr(getattr(item, "action", None), "sources", None) or []:
                url = getattr(source, "url", None)
                if isinstance(url, str):
                    sources.append(url)
        elif kind == "message":
            for part in getattr(item, "content", None) or []:
                for note in getattr(part, "annotations", None) or []:
                    url = getattr(note, "url", None)
                    if getattr(note, "type", None) == "url_citation" and isinstance(url, str):
                        annotations.append(url)
    return {
        "annotation_urls": list(dict.fromkeys(annotations)),
        "source_urls": list(dict.fromkeys(sources)),
        "search_calls": searches,
    }


def claimed_not_reported(claimed: Sequence[str], reported: Sequence[str]) -> list[str]:
    known = {normalize_url(url) for url in reported}
    return [url for url in claimed if normalize_url(url) not in known]


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


def tokens(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    values = []
    for name in ("input_tokens", "output_tokens"):
        reported = getattr(usage, name, None)
        ok = isinstance(reported, int) and not isinstance(reported, bool)
        values.append(reported if ok else 0)
    return values[0], values[1]


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
    parser.add_argument("--answer", choices=("number", "text"), default="text")
    parser.add_argument("--unit", help="Unit of a number answer, e.g. m")
    parser.add_argument("--expected-column", help="Header of a column with the right answers")
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
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--price-per-search", type=float, default=DEFAULT_PRICE_PER_SEARCH)
    parser.add_argument("--price-input-per-mtok", type=float, default=DEFAULT_PRICE_INPUT_PER_MTOK)
    parser.add_argument(
        "--price-output-per-mtok", type=float, default=DEFAULT_PRICE_OUTPUT_PER_MTOK
    )
    parser.add_argument("--out", type=Path, default=Path("artifacts/web-lookup-experiments"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request for the first row and send nothing",
    )
    return parser


def run(argv: Sequence[str] | None = None, client: Any = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.rows < 1 or args.header_row < 1 or args.concurrency < 1:
        parser.error("--rows, --header-row and --concurrency must be positive")
    if args.max_tool_calls < 0 or args.max_output_tokens < 1 or args.timeout <= 0:
        parser.error("--max-tool-calls, --max-output-tokens and --timeout are out of range")
    if args.unit and args.answer != "number":
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
