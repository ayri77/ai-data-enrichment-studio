"""Reproduce both official workbook API runs. Offline fixtures are the default."""

import argparse
import json
from contextlib import AbstractContextManager
from hashlib import sha256
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from ai_data_enrichment.api import create_app
from ai_data_enrichment.evaluation.plan_run_fixtures import FixturePlanClient, offline_executor
from ai_data_enrichment.plan_run import TERMINAL, PlanRunSettings


def run_dataset(client: httpx.Client | TestClient, name: str, output: Path, args) -> None:
    source = Path("samples") / f"{name}.xlsx"
    source_bytes = source.read_bytes()
    plan = json.loads(Path(f"demo/{name}_api_plan.json").read_text("utf-8"))
    uploaded = client.post("/api/datasets", params={"filename": source.name}, content=source_bytes)
    uploaded.raise_for_status()
    draft = client.post(
        "/api/plans",
        json={
            "dataset_id": uploaded.json()["dataset_id"],
            "sheet_name": "Sheet1",
            "range_ref": "A1:E11" if name == "capitals" else "A1:C11",
            "instruction": (
                "Compute distances in km between the literal city pairs and countries. "
                "Write only distance to column distance, including Istanbul as the literal city."
                if name == "capitals"
                else "Find verified mountain elevations in metres. Write the value to height."
            ),
            "output_columns": {"distance": "E"} if name == "capitals" else {"height": "C"},
            "allow_network": args.allow_network,
            "mountain_resolution_policy": args.mountain_policy,
            **({} if args.generate else {"plan": plan}),
        },
    )
    draft.raise_for_status()
    decision = draft.json()
    if not decision["validation"]["valid"]:
        raise RuntimeError(f"Plan requires repair: {decision['validation']}")
    endpoint = f"/api/plans/{decision['plan_id']}"
    validated = client.post(endpoint + "/validate")
    validated.raise_for_status()
    approval = client.post(
        endpoint + "/approve",
        json={
            "approved": True,
            "fingerprint": decision["fingerprint"],
        },
    )
    approval.raise_for_status()
    started = client.post(
        endpoint + "/runs",
        json={
            "allow_network": args.allow_network,
            "timeout_seconds": args.timeout,
        },
    )
    started.raise_for_status()
    job_id = started.json()["job_id"]
    deadline = monotonic() + args.timeout + 15
    while monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] in TERMINAL:
            break
        sleep(0.1)
    else:
        client.post(f"/api/jobs/{job_id}/cancel").raise_for_status()
        raise RuntimeError("Polling deadline exceeded; cancellation requested.")
    report = client.get(job["artifacts"]["report"])
    report.raise_for_status()
    (output / f"{name}.report.json").write_bytes(report.content)
    assert source.read_bytes() == source_bytes
    assert report.json()["source_sha256"] == sha256(source_bytes).hexdigest()
    if "result" not in job["artifacts"]:
        raise RuntimeError(f"Run ended {job['status']}; see downloaded report.")
    result = client.get(job["artifacts"]["result"])
    result.raise_for_status()
    (output / f"{name}.enriched.xlsx").write_bytes(result.content)
    assert report.json()["result_sha256"] == sha256(result.content).hexdigest()
    print(f"{name}: {job['status']} ({job['execution_mode']}), {job['metrics']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument(
        "--generate", action="store_true", help="Call the real LLMPlanner boundary."
    )
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--timeout", type=int, choices=range(1, 301), default=180, metavar="1..300")
    parser.add_argument("--mountain-policy", choices=("strict", "verified_only"), default="strict")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/m5-plan-run"))
    args = parser.parse_args()
    if args.mode == "live" and not args.allow_network:
        parser.error("Live mode requires --allow-network and an explicitly enabled local server.")
    output = args.output_dir / uuid4().hex
    output.mkdir(parents=True)
    for name in ("capitals", "mountains"):
        context: AbstractContextManager
        if args.mode == "offline":
            fixture = FixturePlanClient(json.loads(Path(f"demo/{name}_api_plan.json").read_text()))
            context = TestClient(
                create_app(
                    settings=PlanRunSettings(),
                    offline_plan_client=fixture,
                    offline_executor_factory=offline_executor,
                )
            )
        else:
            context = httpx.Client(base_url=args.url, timeout=45)
        with context as client:
            run_dataset(client, name, output, args)
    print(f"Downloaded workbooks and reports: {output.resolve()}")


if __name__ == "__main__":
    main()
