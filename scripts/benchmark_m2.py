"""Run a fully offline, measured M2 acceptance benchmark (no API or LLM)."""

import argparse
import json

from ai_data_enrichment.evaluation.m2_benchmark import run_acceptance


def main() -> int:
    parser = argparse.ArgumentParser(description="M2 audited, offline DAG benchmark")
    parser.add_argument(
        "--rows", type=int, default=1000, help="Input rows per main scenario (1–1000)"
    )
    args = parser.parse_args()
    if not 1 <= args.rows <= 1000:
        parser.error("--rows must be between 1 and 1000")
    report = run_acceptance(args.rows)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
