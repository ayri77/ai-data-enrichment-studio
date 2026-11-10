"""Explicit offline-only app for Playwright, using the existing injected adapters.

Run from the repository root. Standard ai_data_enrichment.api:app is unchanged.
There are no new HTTP fixture switches or simulated execution results.
"""

import json
from pathlib import Path
from typing import Any

from ai_data_enrichment.api import create_app
from ai_data_enrichment.evaluation.plan_run_fixtures import FixturePlanClient, offline_executor


class SamplePlanClient:
    def complete(self, *, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> str:
        context = json.loads(messages[1]["content"])
        columns = {column["name"] for column in context["dataset_profile"]["columns"]}
        name = "capitals" if "distance" in columns else "mountains"
        plan = json.loads(Path(f"demo/{name}_api_plan.json").read_text("utf-8"))
        return FixturePlanClient(plan).complete(messages=messages, response_schema=response_schema)


app = create_app(offline_plan_client=SamplePlanClient(), offline_executor_factory=offline_executor)
