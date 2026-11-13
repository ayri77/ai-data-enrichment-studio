"""Repeat real LLM planning experiments without the UI or executing any tools.

Run it as a module from the repository root, so that ``tests.support`` is importable:

    uv run --env-file .env python -m scripts.experiment_planner --case capitals --model gpt-4.1-mini

A reasoning model is measured with its effort stated, because the default effort is the
model's own and its reasoning tokens count against the output budget:

    ... --model gpt-6-luna --reasoning-effort low

Results are stored locally in ignored artifacts/planner-experiments/.
"""

import argparse
import json
import os
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from openai import OpenAI
from tests.support.planning import (
    CAPITALS_INSTRUCTION,
    MOUNTAINS_INSTRUCTION,
    workbook_profile,
)

from ai_data_enrichment.plan_run_worker import MAX_PLAN_STEPS, builtin_registry
from ai_data_enrichment.planning.llm_planner import LLMPlanner, PlanGenerationError
from ai_data_enrichment.planning.openai_client import OpenAIPlanClient
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy


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


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("capitals", "mountains"), required=True)
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
    args = parser.parse_args()

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
        else CAPITALS_INSTRUCTION
        if args.case == "capitals"
        else MOUNTAINS_INSTRUCTION
    )
    if not instruction:
        parser.error("Instruction cannot be empty")

    # What the application offers in this environment: a road distance is part
    # of it only with a routing key, and the planner is measured on that set.
    registry = builtin_registry()
    offered = sorted(spec.id for spec in registry.list_all())
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
            outcomes = [(a["outcome"], a["diagnostic_codes"]) for a in record.get("attempts", [])]
            print(
                f"[{index}/{args.repeat}] {record['status']} | "
                f"valid={record['valid']} | attempts={outcomes} | "
                f"seconds={record['request_seconds']}"
            )

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
        "success_count": sum(
            r.get("status") == "ready" and r.get("valid") is True for r in records
        ),
        "runs": records,
    }
    report_path = experiment_dir / "summary.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nValid ready plans: {summary['success_count']}/{args.repeat}")
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
