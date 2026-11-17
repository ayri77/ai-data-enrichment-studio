"""Repeat real LLM planning experiments without the UI or executing any tools.

Run it as a module from the repository root, so that ``tests.support`` is importable:

    uv run --env-file .env python -m scripts.experiment_planner --case capitals --model gpt-4.1-mini

The three-column case mirrors the interface when the output targets are stated:

    ... --case capitals_roads --output-targets distance,road,detour

Latitude and longitude of a city in two new columns of the Capitals table:

    ... --case capitals_coordinates --output-targets latitude,longitude

The population of a city in one new column of the Capitals table:

    ... --case capitals_population --output-targets population

The partly filled table (empty cells are filled, the others kept):

    ... --case mountains_gaps --output-targets height --repeat 10

A task where one column has no source, so that the planner should name it as unfilled and
fill the other (the route check then expects exactly that column to be handed away):

    ... --case mountains_age --output-targets height,age
    ... --case capitals_mayor --output-targets distance,mayor

A reasoning model is measured with its effort stated, because the default effort is the
model's own and its reasoning tokens count against the output budget:

    ... --model gpt-6-luna --reasoning-effort low

Every valid plan is also compared with the expected route of its case (scripts/planner_routes.py):
the right capability, parameters and sources for each output column, not only a plan that passes
validation. The headline of a measurement is "N of M: valid and on the expected route".

Plans saved by an earlier run directory can be judged again without calling the model:

    uv run python -m scripts.experiment_planner --routes-only artifacts/planner-experiments/<run>

Results are stored locally in ignored artifacts/planner-experiments/. A saved plan holds column
headers and capability names; it can also hold a cell value where the model wrote a literal
input, a parameter or an assumption (and the model's reason, in summary.json), so treat the
directory as data, not only as structure.
"""

import argparse
import json
import os
import subprocess
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from openai import OpenAI
from pydantic import ValidationError
from scripts.planner_routes import ROUTES, check_route
from tests.support.planning import (
    CAPITALS_COORDINATES_INSTRUCTION,
    CAPITALS_INSTRUCTION,
    CAPITALS_MAYOR_INSTRUCTION,
    CAPITALS_POPULATION_INSTRUCTION,
    CAPITALS_ROADS_INSTRUCTION,
    MOUNTAINS_AGE_INSTRUCTION,
    MOUNTAINS_GAPS_INSTRUCTION,
    MOUNTAINS_INSTRUCTION,
    workbook_profile,
)

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan, LiteralValue
from ai_data_enrichment.plan_run_worker import MAX_PLAN_STEPS, builtin_registry
from ai_data_enrichment.planning.llm_planner import LLMPlanner, PlanGenerationError
from ai_data_enrichment.planning.openai_client import OpenAIPlanClient
from ai_data_enrichment.providers.openrouteservice import ROUTING_KEY_VARIABLE
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy

# The sample tasks, each with the instruction a visitor of the demo gets.
INSTRUCTIONS = {
    "capitals": CAPITALS_INSTRUCTION,
    "mountains": MOUNTAINS_INSTRUCTION,
    # The same table with some heights filled in: does the planner accept a
    # "fill the gaps" task and keep the plan the empty table gets?
    "mountains_gaps": MOUNTAINS_GAPS_INSTRUCTION,
    # Five steps and three columns; needs a routing key in the environment.
    "capitals_roads": CAPITALS_ROADS_INSTRUCTION,
    # One geocode step written into two new columns; the Capitals table, no routing key.
    "capitals_coordinates": CAPITALS_COORDINATES_INSTRUCTION,
    # One geocode step, one new column: a fact of the found place, not its position.
    "capitals_population": CAPITALS_POPULATION_INSTRUCTION,
    # Two columns asked for, one of them with no source in the system: the planner may
    # answer "ready" for the column it can fill and name the other as unfilled.
    "mountains_age": MOUNTAINS_AGE_INSTRUCTION,
    "capitals_mayor": CAPITALS_MAYOR_INSTRUCTION,
}
# What each case must produce, and how, is in scripts/planner_routes.py: ROUTES has the same keys.


class RecordingClient:
    """Record model outputs for opt-in local diagnosis; never record API keys."""

    def __init__(self, delegate: OpenAIPlanClient) -> None:
        self.delegate = delegate
        self.completions: list[dict[str, object]] = []

    def complete(self, *, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> str:
        record: dict[str, object] = {"call": len(self.completions) + 1}
        self.completions.append(record)
        started = perf_counter()
        try:
            output = self.delegate.complete(messages=messages, response_schema=response_schema)
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            # One request to the model, as the server would wait for it.
            record["seconds"] = round(perf_counter() - started, 2)
        record["response"] = output
        record["messages"] = messages  # Saved to disk only with --capture-raw.
        return output


def source_revision() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


# What the application itself allows one planning request (plan_run.py): a model
# that is right but slower than this fails for a user of the interface.
SERVER_REQUEST_TIMEOUT_SECONDS = 30
SERVER_MAX_OUTPUT_TOKENS = 4096


def request_timing(records: list[dict[str, object]]) -> dict[str, object]:
    """Summarize single-request durations across runs, repairs included."""
    seconds = [
        float(value)
        for record in records
        for value in cast(list[float], record.get("request_seconds", []))
    ]
    return {
        "requests": len(seconds),
        "median_seconds": round(median(seconds), 2) if seconds else None,
        "max_seconds": max(seconds) if seconds else None,
        "over_limit": sum(value > SERVER_REQUEST_TIMEOUT_SECONDS for value in seconds),
    }


def judge_route(record: dict[str, object], case: str) -> None:
    """Add the route verdict to a run. Only a valid, ready plan is judged."""
    route: dict[str, object] = {"verdict": "not_judged", "deviations": []}
    record["route"] = route
    plan = record.get("plan")
    if not (record.get("status") == "ready" and record.get("valid") is True and plan):
        return
    try:
        parsed = EnrichmentPlan.model_validate(plan)
    except ValidationError:
        route["unreadable_plan"] = True  # A plan of an older schema: neither on nor off route.
        return
    columns = [column.name for column in workbook_profile(case).columns]
    unfilled = cast(list[str], record.get("unfilled_targets") or [])
    verdict = check_route(parsed, ROUTES[case], columns, unfilled)
    route["verdict"] = "expected" if verdict.on_route else "deviates"
    route["deviations"] = [
        {"code": d.code.value, "message": d.message, "blocking": d.blocking}
        for d in verdict.deviations
    ]


def route_line(record: dict[str, object]) -> str:
    route = cast(dict[str, Any], record.get("route") or {})
    deviations = cast(list[dict[str, Any]], route.get("deviations", []))
    blocking = list(dict.fromkeys(d["code"] for d in deviations if d["blocking"]))
    notes = list(dict.fromkeys(d["code"] for d in deviations if not d["blocking"]))
    verdict = route.get("verdict")
    if verdict == "deviates":
        return f"route: deviates ({', '.join(blocking)})"
    if verdict == "expected":
        return f"route: expected ({', '.join(notes)})" if notes else "route: expected"
    if route.get("unreadable_plan"):
        return "route: not judged (plan unreadable)"
    if record.get("status") in ("unsupported", "ambiguous"):
        return "route: not judged (refused)"
    return "route: not judged (no valid plan)"


def run_line(record: dict[str, object], index: int, total: int) -> str:
    outcomes = [
        (a["outcome"], a["diagnostic_codes"])
        for a in cast(list[dict[str, Any]], record.get("attempts", []))
    ]
    unfilled = record.get("unfilled_targets") or []
    return (
        f"[{index}/{total}] {record['status']} | valid={record['valid']} | "
        f"attempts={outcomes} | seconds={record.get('request_seconds', [])}"
        + (f" | unfilled={unfilled}" if unfilled else "")
    )


def has_literal_input(record: dict[str, object]) -> bool:
    """A literal is the one place where a plan can hold a value copied from a cell."""
    plan = record.get("plan")
    if not plan:
        return False
    try:
        parsed = EnrichmentPlan.model_validate(plan)
    except ValidationError:
        return False
    return any(
        isinstance(reference, LiteralValue)
        for step in parsed.steps
        for reference in step.inputs.values()
    )


def route_summary(records: list[dict[str, object]], total: int) -> dict[str, object]:
    """The numbers of a measurement; ``success_count`` is the validity count of old."""
    valid = [r for r in records if r.get("status") == "ready" and r.get("valid") is True]
    verdicts = [cast(dict[str, Any], r.get("route") or {}).get("verdict") for r in valid]
    on_route = verdicts.count("expected")
    deviating = [r for r, v in zip(valid, verdicts, strict=True) if v == "deviates"]
    counts: Counter[str] = Counter()
    notes: Counter[str] = Counter()
    for record in valid:
        deviations = cast(list[dict[str, Any]], cast(dict[str, Any], record["route"])["deviations"])
        counts.update({d["code"] for d in deviations if d["blocking"]})
        notes.update({d["code"] for d in deviations if not d["blocking"]})
    refused = sum(r.get("status") in ("unsupported", "ambiguous") for r in records)
    # Valid plans that listed a target as having no source where the case expects a source.
    handed_away = sum(
        any(
            d["code"] == "TARGET_HANDED_AWAY"
            for d in cast(list[dict[str, Any]], cast(dict[str, Any], r["route"])["deviations"])
        )
        for r in valid
    )
    return {
        "success_count": len(valid),
        "valid_on_expected_route": on_route,
        "valid_deviating_route": len(deviating),
        "valid_not_judged": len(valid) - on_route - len(deviating),
        "deviation_counts": dict(sorted(counts.items())),
        "note_counts": dict(sorted(notes.items())),
        "refused": refused,
        "invalid": len(records) - len(valid) - refused,
        "plans_with_literal_inputs": sum(has_literal_input(r) for r in records),
        "valid_handed_away_expected_filled": handed_away,
        "headline": f"{on_route} of {total}: valid and on the expected route",
    }


def print_route_summary(summary: dict[str, object], total: int) -> None:
    print(f"\nValid ready plans: {summary['success_count']}/{total}")
    counts = cast(dict[str, int], summary["deviation_counts"])
    detail = ", ".join(f"{code} x{n}" for code, n in counts.items())
    print(f"Valid plans on the expected route: {summary['valid_on_expected_route']}/{total}")
    print(
        f"Valid plans that deviate: {summary['valid_deviating_route']}/{total}"
        + (f" ({detail})" if detail else "")
    )
    notes = cast(dict[str, int], summary["note_counts"])
    if notes:
        print(
            "Noted, not failed: " + ", ".join(f"{code} in {n} run(s)" for code, n in notes.items())
        )
    print(
        "Valid plans that handed away a target the case expects to be filled: "
        f"{summary['valid_handed_away_expected_filled']}/{total}"
    )
    print(f"Refused: {summary['refused']} | invalid: {summary['invalid']}")
    print(f"Headline: {summary['headline']}")
    if summary["plans_with_literal_inputs"]:
        print(
            f"WARNING: {summary['plans_with_literal_inputs']} plan(s) contain a literal input, "
            "which can hold a value copied from a cell; the saved plans are data, not only "
            "structure."
        )


def rejudge(directory: Path, expected_case: str | None = None) -> dict[str, object]:
    """Judge again the plans saved by an earlier run, without a model or a network."""
    path = directory / "summary.json"
    if not path.is_file():
        raise FileNotFoundError(f"No summary.json in {directory}")
    saved = json.loads(path.read_text(encoding="utf-8"))
    case = saved.get("case")
    if case not in ROUTES:
        raise ValueError(f"No expected route for case {case!r}")
    if expected_case and expected_case != case:
        raise ValueError(f"{directory} holds case {case}, not {expected_case}")
    records = cast(list[dict[str, object]], saved["runs"])
    for index, record in enumerate(records, start=1):
        judge_route(record, case)
        print(f"{run_line(record, index, len(records))}\n    {route_line(record)}")
    summary = {
        "case": case,
        "source": str(path),
        **route_summary(records, len(records)),
        "runs": [
            {
                "run": r.get("run"),
                "status": r.get("status"),
                "valid": r.get("valid"),
                "unfilled_targets": r.get("unfilled_targets") or [],
                "route": r["route"],
            }
            for r in records
        ],
    }
    print_route_summary(summary, len(records))
    (directory / "routes.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=tuple(INSTRUCTIONS))
    parser.add_argument(
        "--routes-only",
        type=Path,
        metavar="DIRECTORY",
        help="Judge the routes of the plans saved by an earlier run directory (its "
        "summary.json), without calling the model; writes routes.json beside it",
    )
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL"))
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--repairs", type=int, choices=(0, 1, 2), default=2)
    parser.add_argument("--prompt-file", type=Path, help="Override the standard test instruction")
    parser.add_argument(
        "--capture-raw",
        action="store_true",
        help="Save raw LLM responses and prompts (public fixtures only)",
    )
    parser.add_argument(
        "--output-targets",
        help="Comma-separated target column names, as the UI sends them (e.g. distance)",
    )
    parser.add_argument(
        "--reasoning-effort",
        help="For reasoning models only (e.g. none, low, medium); omitted from the request "
        "when not given, which leaves the model's own default",
    )
    parser.add_argument("--max-output-tokens", type=int, default=SERVER_MAX_OUTPUT_TOKENS)
    parser.add_argument("--out", type=Path, default=Path("artifacts/planner-experiments"))
    args = parser.parse_args(argv)

    if args.routes_only:
        try:
            rejudge(args.routes_only, args.case)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(f"Report: {(args.routes_only / 'routes.json').resolve()}")
        return
    if not args.case:
        parser.error("Use --case, or --routes-only with a saved run directory")
    if not 1 <= args.repeat <= 10:
        parser.error("--repeat must be between 1 and 10")
    if not args.model:
        parser.error("Use --model or set OPENAI_MODEL")
    if args.max_output_tokens < 1:
        parser.error("--max-output-tokens must be positive")
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY in the local shell or an ignored .env file")

    instruction = (
        args.prompt_file.read_text(encoding="utf-8").strip()
        if args.prompt_file
        else INSTRUCTIONS[args.case]
    )
    if not instruction:
        parser.error("Instruction cannot be empty")

    # What the application offers in this environment: a road distance is part
    # of it only with a routing key, and the planner is measured on that set.
    registry = builtin_registry()
    offered = sorted(spec.id for spec in registry.list_all())
    if args.case == "capitals_roads" and "road_distance" not in offered:
        parser.error(f"--case capitals_roads needs {ROUTING_KEY_VARIABLE} in the environment")
    print(f"Capabilities offered to the planner: {', '.join(offered)}")

    experiment_dir = args.out / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    experiment_dir.mkdir(parents=True, exist_ok=False)
    profile = workbook_profile(args.case)
    targets = [name.strip() for name in (args.output_targets or "").split(",") if name.strip()]
    # With targets, mirror the UI/API planning policy; without, keep the unbound baseline.
    policy = (
        PlanValidationPolicy(max_steps=MAX_PLAN_STEPS, output_targets=frozenset(targets))
        if targets
        else None
    )
    records: list[dict[str, object]] = []

    with OpenAI(timeout=45.0, max_retries=0) as api:
        for index in range(1, args.repeat + 1):
            adapter = RecordingClient(
                OpenAIPlanClient(
                    api=api,
                    model=args.model,
                    max_output_tokens=args.max_output_tokens,
                    reasoning_effort=args.reasoning_effort,
                )
            )
            planner = LLMPlanner(
                client=adapter, registry=registry, max_repair_attempts=args.repairs
            )
            record: dict[str, object] = {"run": index}
            try:
                result = planner.generate(
                    instruction=instruction, dataset_profile=profile, validation_policy=policy
                )
                record.update(
                    status=result.response.status,
                    reason=result.response.reason,
                    unfilled_targets=list(result.unfilled_targets),
                    valid=result.validation.valid if result.validation else False,
                    attempts=[asdict(a) for a in result.attempts],
                    diagnostics=(
                        [e.model_dump(mode="json") for e in result.validation.errors]
                        if result.validation
                        else []
                    ),
                    plan=(
                        result.response.plan.model_dump(mode="json")
                        if result.response.plan
                        else None
                    ),
                )
            except PlanGenerationError as exc:
                record.update(
                    status="planning_error",
                    valid=False,
                    error_type=type(exc).__name__,
                    attempts=[asdict(a) for a in exc.attempts],
                )
            except Exception as exc:
                record.update(status="unexpected_error", valid=False, error_type=type(exc).__name__)

            if args.capture_raw:
                (experiment_dir / f"run-{index:02d}-raw.json").write_text(
                    json.dumps(adapter.completions, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            # Kept with the run even without --capture-raw: timing is not content.
            record["request_seconds"] = [c["seconds"] for c in adapter.completions]
            records.append(record)
            judge_route(record, args.case)
            print(f"{run_line(record, index, args.repeat)}\n    {route_line(record)}")

    timing = request_timing(records)
    summary = {
        "case": args.case,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "max_output_tokens": args.max_output_tokens,
        "capabilities": offered,
        "request_timing": timing,
        "git_revision": source_revision(),
        "repeat": args.repeat,
        "max_repair_attempts": args.repairs,
        "output_targets": targets or None,
        "prompt": instruction,  # Only official sample instructions, unless overridden.
        **route_summary(records, args.repeat),
        "runs": records,
    }
    report_path = experiment_dir / "summary.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print_route_summary(summary, args.repeat)
    if timing["requests"]:
        print(
            f"Requests: {timing['requests']} | median {timing['median_seconds']} s | "
            f"slowest {timing['max_seconds']} s | "
            f"over the server's {SERVER_REQUEST_TIMEOUT_SECONDS} s limit: {timing['over_limit']}"
        )
    print(f"Report: {report_path.resolve()}")
    if args.capture_raw:
        print("Raw completions/prompts were captured locally; do not commit or share unreviewed.")


if __name__ == "__main__":
    run()
