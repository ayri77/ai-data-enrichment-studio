"""Explicit offline-only app for Playwright, using the existing injected adapters.

Run from the repository root. Standard ai_data_enrichment.api:app is unchanged.
There are no new HTTP fixture switches or simulated execution results.
"""

import json
import os
from pathlib import Path
from typing import Any

from ai_data_enrichment.api import create_app
from ai_data_enrichment.evaluation.plan_run_fixtures import (
    FixtureNamesClient,
    FixturePlanClient,
    FixtureTasksClient,
    asks_for_names,
    asks_for_tasks,
    offline_executor,
)


class SamplePlanClient:
    def complete(self, *, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> str:
        if asks_for_tasks(response_schema):
            return FixtureTasksClient().complete(messages=messages, response_schema=response_schema)
        if asks_for_names(response_schema):
            return FixtureNamesClient().complete(messages=messages, response_schema=response_schema)
        context = json.loads(messages[1]["content"])
        columns = {column["name"] for column in context["dataset_profile"]["columns"]}
        name = (
            "capitals_roads"
            if "road" in columns
            else "capitals"
            if "distance" in columns
            else "mountains"
        )
        plan = json.loads(Path(f"demo/{name}_api_plan.json").read_text("utf-8"))
        return FixturePlanClient(plan).complete(messages=messages, response_schema=response_schema)


# Road distances are offered only with a routing key. The injected transport
# answers every request itself, so this value never leaves the process.
os.environ.setdefault("OPENROUTESERVICE_API_KEY", "offline-fixture")
# The usage summary opens with this code in the browser scenarios; it guards
# nothing but counters of this test process.
USAGE_CODE = "fixture-admin-code"
app = create_app(
    offline_plan_client=SamplePlanClient(),
    offline_executor_factory=offline_executor,
    admin_code=USAGE_CODE,
)
