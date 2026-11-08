"""Offline acceptance tests for the M4 developer demo."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("mode", "expected_nodes", "expected_repairs"),
    [
        (
            "happy",
            ["obtain_plan", "validate", "execute"],
            0,
        ),
        (
            "repair",
            [
                "obtain_plan",
                "validate",
                "repair_plan",
                "validate",
                "execute",
            ],
            1,
        ),
    ],
)
def test_m4_offline_demo(
    mode: str,
    expected_nodes: list[str],
    expected_repairs: int,
) -> None:
    """Both demo modes must complete without LLM or network services."""

    env = os.environ.copy()

    # CI acceptance must not send traces to LangSmith.
    env["LANGSMITH_TRACING"] = "false"
    env["LANGCHAIN_TRACING_V2"] = "false"

    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.demo_m4_agent",
            "--mode",
            mode,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert process.returncode == 0, process.stderr

    report = json.loads(process.stdout)

    assert report["mode"] == mode
    assert report["final_status"] == "completed"
    assert report["repair_attempts"] == expected_repairs

    assert [event["node"] for event in report["trace"]] == expected_nodes

    assert report["metrics"]["total_rows"] == 1
    assert report["metrics"]["successful_rows"] == 1
    assert report["metrics"]["failed_rows"] == 0
    assert report["metrics"]["logical_operations"] == 3
    assert report["metrics"]["executed_handler_calls"] == 3

    # Geographic calculation tolerance, not an exact float comparison.
    assert report["distance_km"] == pytest.approx(
        1204.46,
        abs=0.02,
    )

    if mode == "repair":
        assert "UNSUPPORTED_CAPABILITY" in (report["trace"][1]["diagnostics"])
        assert report["trace"][2]["repair_attempts"] == 1
    else:
        assert all(not event["diagnostics"] for event in report["trace"])
