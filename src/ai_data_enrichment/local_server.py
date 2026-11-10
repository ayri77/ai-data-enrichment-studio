"""Supported single-user launcher; browser key configuration is loopback-only."""

import argparse

import uvicorn

from ai_data_enrichment.api import create_app
from ai_data_enrichment.plan_run import PlanRunSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-network", action="store_true", help="Enable the server network gate"
    )
    args = parser.parse_args()
    defaults = PlanRunSettings.from_environment()
    app = create_app(
        settings=PlanRunSettings(
            allow_network=args.allow_network or defaults.allow_network, llm_model=defaults.llm_model
        ),
        local_browser_configuration=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=8010, proxy_headers=False)


if __name__ == "__main__":
    main()
