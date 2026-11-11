"""Repeat real LLM planning experiments without the UI or executing any tools.

Run it as a module from the repository root, so that ``tests.support`` is importable:

    uv run --env-file .env python -m scripts.experiment_planner --case capitals --model gpt-4.1-mini

Results are stored locally in ignored artifacts/planner-experiments/.
"""

import argparse
import json
import os
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from openai import OpenAI
from tests.support.planning import (
    CAPITALS_INSTRUCTION,
    MOUNTAINS_INSTRUCTION,
    workbook_profile,
)

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.planning.llm_planner import LLMPlanner, PlanGenerationError
from ai_data_enrichment.planning.openai_client import OpenAIPlanClient


class RecordingClient:
    """Record model outputs for opt-in local diagnosis; never record API keys."""

    def __init__(self, delegate: OpenAIPlanClient) -> None:
        self.delegate = delegate
        self.completions: list[dict[str, object]] = []

    def complete(self, *, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> str:
        record: dict[str, object] = {"call": len(self.completions) + 1}
        self.completions.append(record)
        try:
            output = self.delegate.complete(messages=messages, response_schema=response_schema)
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
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
    parser.add_argument("--out", type=Path, default=Path("artifacts/planner-experiments"))
    args = parser.parse_args()

    if not 1 <= args.repeat <= 10:
        parser.error("--repeat must be between 1 and 10")
    if not args.model:
        parser.error("Use --model or set OPENAI_MODEL")
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

    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)

    experiment_dir = args.out / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    experiment_dir.mkdir(parents=True, exist_ok=False)
    profile = workbook_profile(args.case)
    records: list[dict[str, object]] = []

    with OpenAI(timeout=45.0, max_retries=0) as api:
        for index in range(1, args.repeat + 1):
            adapter = RecordingClient(
                OpenAIPlanClient(api=api, model=args.model, max_output_tokens=4096)
            )
            planner = LLMPlanner(
                client=adapter, registry=registry, max_repair_attempts=args.repairs
            )
            record: dict[str, object] = {"run": index}
            try:
                result = planner.generate(instruction=instruction, dataset_profile=profile)
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
            records.append(record)
            outcomes = [(a["outcome"], a["diagnostic_codes"]) for a in record.get("attempts", [])]
            print(
                f"[{index}/{args.repeat}] {record['status']} | "
                f"valid={record['valid']} | attempts={outcomes}"
            )

    summary = {
        "case": args.case,
        "model": args.model,
        "git_revision": source_revision(),
        "repeat": args.repeat,
        "max_repair_attempts": args.repairs,
        "prompt": instruction,  # Only official sample instructions, unless overridden.
        "success_count": sum(
            r.get("status") == "ready" and r.get("valid") is True for r in records
        ),
        "runs": records,
    }
    report_path = experiment_dir / "summary.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nValid ready plans: {summary['success_count']}/{args.repeat}")
    print(f"Report: {report_path.resolve()}")
    if args.capture_raw:
        print("Raw completions/prompts were captured locally; do not commit or share unreviewed.")


if __name__ == "__main__":
    run()
