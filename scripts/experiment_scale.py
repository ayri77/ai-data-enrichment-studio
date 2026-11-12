"""Developer measurement: how does one run behave on hundreds or thousands of rows?

Run as a module from the repository root:

    uv run python -m scripts.experiment_scale --case capitals --rows 100
    uv run python -m scripts.experiment_scale --case capitals --rows 500 --distinct 60
    uv run python -m scripts.experiment_scale --case mountains --rows 50
    uv run python -m scripts.experiment_scale --case capitals --rows 100 --demo-limits

It builds a test workbook (see `scripts.scale_workbook`), then drives the same
HTTP API the interface uses, in this process: upload, the example's prepared
plan, approval, one run, the report. No OpenAI key is needed, because the
prepared plan is submitted as is. The external sources are asked for real, so
results depend on them and on the network.

The demo bounds one run to 20 rows, 400 requests per source and 300 seconds.
This script raises those bounds for its own process through the environment
variables read by `ai_data_enrichment.run_limits`; nothing changes for the
application's defaults. `--demo-limits` raises only the row count, to show
which of the remaining bounds stops a larger run first.

Be considerate: Open-Meteo's free tier allows 10 000 calls a day per IP
address, and Wikidata asks for few concurrent requests. A run is sequential,
but a large mountains table still means thousands of requests.

The summary is printed and written under the ignored
`artifacts/scale-experiments/`. `--offline` uses the recorded fixture answers
instead of the network; it only checks that the script itself works, since the
fixtures know just the ten names of each bundled sample.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Any

from scripts.scale_workbook import build_rows, distinct_lookups, left_out, write_workbook

TERMINAL = {"completed", "needs_review", "failed", "cancelled", "timed_out"}
OUTPUT = {"capitals": {"distance": "E"}, "mountains": {"height": "C"}}


def clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def failure_codes(report: dict[str, Any]) -> Counter[str]:
    """Error codes of steps that did not succeed, counted once per row and step."""
    codes: Counter[str] = Counter()
    for record in report.get("execution", {}).get("records", []):
        for step in record["row_result"]["steps"]:
            result = step["result"]
            if result["status"] != "success":
                codes[f"{step['step_id']}: {result['error_code'] or result['status']}"] += 1
    return codes


def summarize(
    *,
    case: str,
    rows: int,
    lookups: int,
    seconds: float,
    job: dict[str, Any],
    report: dict[str, Any] | None,
    review: dict[str, Any] | None,
    sizes: dict[str, int],
) -> dict[str, Any]:
    execution = (report or {}).get("execution", {})
    providers = execution.get("provider_requests", {})
    attempts = sum(counters["transport_attempts"] for counters in providers.values())
    return {
        "case": case,
        "rows": rows,
        "distinct_lookups": lookups,
        "status": job["status"],
        "error": job.get("error"),
        "seconds": round(seconds, 1),
        "seconds_per_row": round(seconds / rows, 3),
        "metrics": job.get("metrics"),
        "provider_requests": providers,
        "requests_total": attempts,
        "requests_per_row": round(attempts / rows, 2),
        "requests_per_lookup": round(attempts / lookups, 2) if lookups else None,
        "failure_codes": dict(failure_codes(report or {}).most_common()),
        "review_items": len(review["items"]) if review else 0,
        "review_informational": len(review["informational"]) if review else 0,
        "review_affected_rows": review["affected_rows"] if review else 0,
        "bytes": sizes,
        "limits": (report or {}).get("limits"),
        "timeout_seconds": job["timeout_seconds"],
    }


def print_summary(summary: dict[str, Any]) -> None:
    metrics = summary["metrics"] or {}
    print()
    print(f"Status: {summary['status']}", end="")
    if summary["error"]:
        print(f" ({summary['error']['code']}: {summary['error']['message']})", end="")
    print()
    print(
        f"Time: {clock(summary['seconds'])} for {summary['rows']} rows "
        f"({summary['seconds_per_row']} s per row); limit {summary['timeout_seconds']} s"
    )
    if summary["status"] in {"failed", "timed_out", "cancelled"}:
        print("Nothing was published: a run that does not finish leaves no partial result.")
        return
    print(
        f"Rows: {metrics.get('successful_rows')} filled, {metrics.get('partial_rows')} partial, "
        f"{metrics.get('failed_rows')} empty"
    )
    print(
        f"Operations: {metrics.get('logical_operations')} planned, "
        f"{metrics.get('executed_handler_calls')} executed, "
        f"{metrics.get('reused_operations')} answered from the run's memory"
    )
    for provider, counters in summary["provider_requests"].items():
        if not counters["operations_started"] and not counters["transport_attempts"]:
            continue
        print(
            f"{provider}: {counters['transport_attempts']} requests, "
            f"{counters['retry_attempts']} retries, "
            f"{counters['requests_blocked_by_budget']} refused by the request budget, "
            f"{counters['rate_limit_waits']} rate-limit waits "
            f"({counters['rate_limit_wait_seconds']:.1f} s), "
            f"{counters['terminal_transport_failures']} failed for good"
        )
    print(
        f"Requests: {summary['requests_total']} in total, {summary['requests_per_row']} per row, "
        f"{summary['requests_per_lookup']} per different place "
        f"({summary['distinct_lookups']} places)"
    )
    if summary["failure_codes"]:
        print("Steps without a value:")
        for code, count in list(summary["failure_codes"].items())[:8]:
            print(f"  {count:>5}  {code}")
    print(
        f"Review: {summary['review_items']} names to choose for, "
        f"{summary['review_informational']} without a match, "
        f"{summary['review_affected_rows']} rows affected"
    )
    sizes = summary["bytes"]
    print(
        f"Sizes: report {sizes.get('report', 0) / 1024:.0f} KiB, "
        f"workbook {sizes.get('result', 0) / 1024:.0f} KiB"
    )


def run(args: argparse.Namespace, directory: Path = Path("artifacts/scale")) -> dict[str, Any]:
    # Imported after the limits are in the environment: they are read at import.
    from fastapi.testclient import TestClient

    from ai_data_enrichment.api import create_app
    from ai_data_enrichment.evaluation.plan_run_fixtures import offline_executor
    from ai_data_enrichment.plan_run import PlanRunSettings

    rows = build_rows(
        args.case,
        args.rows,
        distinct=args.distinct,
        repeat=args.repeat,
        keep_unrecognized=args.keep_unrecognized,
    )
    lookups = distinct_lookups(args.case, rows)
    workbook = directory / f"{args.case}-{args.rows}.xlsx"
    range_ref = write_workbook(workbook, args.case, rows)
    print(f"{workbook}: {len(rows)} rows, {lookups} different places to look up.")
    if not args.keep_unrecognized and left_out(args.case):
        print(f"Left out (country not recognized): {', '.join(left_out(args.case))}")

    app = create_app(
        settings=PlanRunSettings(allow_network=not args.offline),
        offline_executor_factory=offline_executor if args.offline else None,
    )
    with TestClient(app) as client:

        def call(method: str, url: str, **kwargs: Any) -> Any:
            response = client.request(method, url, **kwargs)
            if response.status_code >= 400:
                raise SystemExit(f"{method} {url} -> {response.status_code}: {response.text}")
            return response

        dataset_id = call(
            "POST",
            "/api/datasets",
            params={"filename": workbook.name},
            content=workbook.read_bytes(),
        ).json()["dataset_id"]
        plan = call(
            "POST",
            "/api/plans",
            json={
                "dataset_id": dataset_id,
                "sheet_name": "Sheet1",
                "range_ref": range_ref,
                "instruction": f"Scale experiment: the prepared {args.case} plan.",
                "output_columns": OUTPUT[args.case],
                "plan": json.loads(Path(f"demo/{args.case}_api_plan.json").read_text("utf-8")),
                "allow_network": not args.offline,
                "mountain_resolution_policy": args.policy,
            },
        ).json()
        if not plan["validation"]["valid"]:
            raise SystemExit(f"The prepared plan is not valid here: {plan['validation']}")
        call(
            "POST",
            f"/api/plans/{plan['plan_id']}/approve",
            json={"approved": True, "fingerprint": plan["fingerprint"]},
        )
        started = monotonic()
        job = call(
            "POST",
            f"/api/plans/{plan['plan_id']}/runs",
            json={"allow_network": not args.offline, "timeout_seconds": args.timeout},
        ).json()
        reported = 0.0
        while job["status"] not in TERMINAL:
            sleep(0.5)
            job = call("GET", f"/api/jobs/{job['job_id']}").json()
            elapsed = monotonic() - started
            if elapsed - reported >= 15:
                reported = elapsed
                print(f"  {job['status']} {clock(elapsed)}", flush=True)
        seconds = monotonic() - started

        report = review = None
        sizes: dict[str, int] = {}
        for artifact in job["artifacts"]:
            content = call("GET", f"/api/jobs/{job['job_id']}/artifacts/{artifact}").content
            sizes[artifact] = len(content)
            if artifact == "report":
                report = json.loads(content)
        if job["status"] == "needs_review":
            review = call("GET", f"/api/jobs/{job['job_id']}/review").json()
    return summarize(
        case=args.case,
        rows=len(rows),
        lookups=lookups,
        seconds=seconds,
        job=job,
        report=report,
        review=review,
        sizes=sizes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--case", choices=sorted(OUTPUT), required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--distinct", type=int, help="capitals only: how many capitals take part")
    parser.add_argument("--repeat", action="store_true", help="mountains only: cycle the list")
    parser.add_argument(
        "--keep-unrecognized",
        action="store_true",
        help="keep entries whose country name the application does not recognize",
    )
    parser.add_argument(
        "--policy",
        choices=("verified_only", "strict"),
        default="verified_only",
        help="single-match policy; verified_only is what the interface uses",
    )
    parser.add_argument("--timeout", type=int, default=1800, help="seconds allowed for the run")
    parser.add_argument(
        "--max-requests", type=int, default=20000, help="requests allowed per source"
    )
    parser.add_argument(
        "--max-wait", type=int, default=600, help="seconds of pacing and retry waits per source"
    )
    parser.add_argument(
        "--demo-limits",
        action="store_true",
        help="keep the demo's request and time bounds; raise only the row count",
    )
    parser.add_argument("--offline", action="store_true", help="fixture answers, no network")
    args = parser.parse_args()
    if not 1 <= args.rows <= 5000:
        parser.error("--rows must be between 1 and 5000 (an upload holds at most 5000 rows).")

    os.environ["ENRICHMENT_MAX_RUN_ROWS"] = str(max(args.rows, 20))
    if args.demo_limits:
        args.timeout = min(args.timeout, 300)
    else:
        os.environ["ENRICHMENT_MAX_TIMEOUT_SECONDS"] = str(args.timeout)
        os.environ["ENRICHMENT_MAX_PROVIDER_REQUESTS"] = str(args.max_requests)
        os.environ["ENRICHMENT_MAX_PROVIDER_WAIT_SECONDS"] = str(args.max_wait)

    try:
        summary = run(args)
    except ValueError as error:
        parser.error(str(error))
    print_summary(summary)
    directory = Path("artifacts/scale-experiments")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{args.case}-{args.rows}-{stamp}.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSummary: {path}")


if __name__ == "__main__":
    main()
