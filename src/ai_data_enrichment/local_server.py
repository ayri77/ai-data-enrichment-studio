"""Supported single-user launcher; browser key configuration is loopback-only.

With `ENRICHMENT_ADMIN_CODE` in the environment the usage summary is available
at `/#usage`, as on a hosted demo; everything counts under the label "local".
Access codes are a matter of the hosted entry point and are not read here.
"""

import argparse
from dataclasses import replace

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
        # Every setting of the environment is kept; only the network gate can also be
        # opened by the flag. Naming the fields one by one here once dropped a new one.
        settings=replace(defaults, allow_network=args.allow_network or defaults.allow_network),
        local_browser_configuration=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=8010, proxy_headers=False)


if __name__ == "__main__":
    main()
