"""Developer entry point: offline by default, explicit live opt-in.

Run from the repository root:
uv run python scripts/smoke_live_providers.py --scenario mountains --mode live --allow-network
"""

from ai_data_enrichment.evaluation.smoke import main

if __name__ == "__main__":
    raise SystemExit(main())
