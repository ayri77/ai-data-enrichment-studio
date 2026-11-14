"""Supported single-user launcher; browser key configuration is loopback-only.

With `ENRICHMENT_ADMIN_CODE` in the environment the usage summary is available
at `/#usage`, as on a hosted demo; everything counts under the label "local".
Access codes are a matter of the hosted entry point and are not read here.
"""

import argparse

import uvicorn

from ai_data_enrichment.api import create_app
from ai_data_enrichment.hosted import admin_code
from ai_data_enrichment.plan_run import PlanRunSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-network", action="store_true", help="Enable the server network gate"
    )
    args = parser.parse_args()
    defaults = PlanRunSettings.from_environment()
    try:
        admin = admin_code()
    except RuntimeError as error:
        parser.error(str(error))
    app = create_app(
        admin_code=admin,
        settings=PlanRunSettings(
            allow_network=args.allow_network or defaults.allow_network, llm_model=defaults.llm_model
        ),
        local_browser_configuration=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=8010, proxy_headers=False)


if __name__ == "__main__":
    main()
