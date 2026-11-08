"""Run reproducible offline M3 Excel benchmarks."""

import argparse
import json
from pathlib import Path

from ai_data_enrichment.evaluation.benchmark_scenarios import (
    BenchmarkScenario,
    load_scenario,
)
from ai_data_enrichment.evaluation.m3_excel_benchmark import (
    run_benchmark,
    run_configured_benchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="M3 offline Excel end-to-end benchmark")
    parser.add_argument(
        "--scenario",
        type=Path,
        help="Path to a benchmark scenario JSON file",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=None,
        help="Override row count (1–1000); default is 8 without a scenario",
    )
    parser.add_argument(
        "--profile-memory",
        action="store_true",
        help="Measure peak Python allocations using tracemalloc",
    )

    args = parser.parse_args()

    if args.rows is not None and not 1 <= args.rows <= 1000:
        parser.error("--rows must be between 1 and 1000")

    if args.scenario is not None:
        scenario = load_scenario(args.scenario)

        if args.rows is not None:
            scenario = BenchmarkScenario.model_validate(
                {
                    **scenario.model_dump(),
                    "rows": args.rows,
                }
            )

        result = run_configured_benchmark(
            scenario,
            profile_memory=args.profile_memory,
        )
    else:
        result = run_benchmark(
            rows=args.rows if args.rows is not None else 8,
            profile_memory=args.profile_memory,
        )

    print(
        json.dumps(
            result.model_dump(mode="json"),
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
