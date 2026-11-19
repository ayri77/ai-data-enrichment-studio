"""Explicit offline-only app for Playwright, using the existing injected adapters.

Run from the repository root. Standard ai_data_enrichment.api:app is unchanged.
There are no new HTTP fixture switches or simulated execution results.
"""

import atexit
import dataclasses
import json
import os
import re
import shutil
import tempfile
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
from ai_data_enrichment.plan_run import PlanRunSettings
from tests.support.extraction_api import LoggedExtractionApiFactory
from tests.support.lookup_api import LoggedApiFactory, mountain_answer
from tests.support.text_extraction import entry
from tests.support.text_extraction import response as extraction_response
from tests.support.web_lookup import PAGE, response, slot

# A task that contains this text is refused by the offline planner, so that the
# scenario for the universal search has a table it cannot plan for.
REFUSED_TASK = "first ascent"
# A task that contains this text gets what a planner sometimes hands over for a
# task no capability covers: status "ready" and a plan with no steps, whose
# outputs point at steps that do not exist. The server's check refuses it.
INVALID_TASK = "phone characteristics"
# A task that contains this text gets what the planner answers when some of the chosen
# columns have a source and others none: a plan for the height of a mountain (the
# mountains sample's plan) and the other chosen columns named as unfilled. The
# interface then offers, or adds, a web search for exactly those columns.
PARTIAL_TASK = "geological age"


class SamplePlanClient:
    def complete(self, *, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> str:
        if asks_for_tasks(response_schema):
            return FixtureTasksClient().complete(messages=messages, response_schema=response_schema)
        if asks_for_names(response_schema):
            return FixtureNamesClient().complete(messages=messages, response_schema=response_schema)
        context = json.loads(messages[1]["content"])
        if REFUSED_TASK in context["instruction"].lower():
            # What the real planner does for a task no capability covers.
            return json.dumps(
                {
                    "status": "unsupported",
                    "plan": None,
                    "reason": "The first ascent of a mountain is not in the sources it can read.",
                }
            )
        if INVALID_TASK in context["instruction"].lower():
            targets = list(context.get("output_targets", [])) or ["Height"]
            return json.dumps(
                {
                    "status": "ready",
                    "plan": {
                        "version": "1.0",
                        "steps": [],
                        "outputs": [
                            {
                                "step_id": f"missing_{number}",
                                "value_field": "value",
                                "target_column": target,
                            }
                            for number, target in enumerate(targets, 1)
                        ],
                        "assumptions": [
                            "No capability available to fill any missing "
                            "phone characteristics fields."
                        ],
                    },
                    "reason": None,
                }
            )
        if PARTIAL_TASK in context["instruction"].lower():
            plan = json.loads(Path("demo/mountains_api_plan.json").read_text("utf-8"))
            targets = list(context.get("output_targets", []))
            return json.dumps(
                {
                    "status": "ready",
                    "plan": plan,
                    "reason": None,
                    "unfilled_targets": [name for name in targets if name != "height"],
                }
            )
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


def lookup_answer(kwargs: dict[str, Any]) -> Any:
    """What the stand-in model says: for the mixed scenario's one column, the age of a mountain.

    A request for the column "age" (the search part of a mixed plan) is answered with one
    slot: stated for Everest, nothing for K2, a conclusion for every other mountain. Every
    other request is the whole-task search of the other scenarios and is answered as before.
    """
    text: str = kwargs["input"]
    if "- value_1 (age):" not in text:
        return mountain_answer(kwargs)
    if "- Mountain: K2" in text:
        return response({"value_1": slot()})
    if "- Mountain: Mount Everest" in text:
        return response(
            {
                "value_1": slot(
                    value="about 60 million years",
                    basis="stated",
                    sources=[PAGE],
                    quote="60 million years",
                )
            }
        )
    return response(
        {
            "value_1": slot(
                value="about 50 million years",
                basis="concluded",
                sources=[PAGE],
                quote="uplift",
            )
        }
    )


def mail_answer(kwargs: dict[str, Any]) -> Any:
    """What the stand-in model says about the ten invented mails in samples/probe/mail.

    The first three targets the browser test fills are Amount (number), Paid on (date) and
    Renews (yes or no); a wider test adds up to five text columns after them. The reply has
    one slot for every column the request lists. The two advertisements are "not about the
    task"; four mails are about payments but give no amount; four receipts give values, each
    quoted from its own text.
    """
    text: str = kwargs["input"]
    slots = len(re.findall(r"^- value_\d+ ", text, flags=re.MULTILINE))

    def reply(applies: bool = True, **entries: dict[str, Any]) -> Any:
        values = {f"value_{i}": entry() for i in range(1, slots + 1)}
        values.update({key: value for key, value in entries.items() if key in values})
        return extraction_response({"applies": applies, "values": values})

    if "Subject: Black Friday" in text or "Subject: New this week" in text:
        return reply(applies=False)
    if "Subject: Ihre Rechnung" in text:
        return reply(
            value_1=entry(10.0, "Gesamt: 10,00 EUR"),
            value_2=entry("2026-11-01", "Bezahlt am 01.11.2026"),
            value_3=entry("Yes", "Ihr Tarif verlängert sich automatisch am 01.12.2026."),
            value_4=entry("Cloudbox Plus", "Cloudbox Plus (2 TB Speicher)"),
            value_5=entry("01.11.2026 bis 30.11.2026", "Zeitraum: 01.11.2026 bis 30.11.2026"),
            value_6=entry("CB-2026-110417", "Rechnung Nr. CB-2026-110417"),
            value_7=entry("19 %", "MwSt. 19 %: 1,60 EUR"),
            value_8=entry("Cloudbox Billing", "From: Cloudbox Billing"),
        )
    if "Subject: Your receipt from PayRelay" in text:
        return reply(
            value_1=entry(29.99, "$29.99"),
            value_2=entry("2026-11-03", "3 Nov 2026"),
            value_3=entry("Yes", "Your plan renews on November 3, 2027."),
            value_4=entry("Notes App Pro (annual plan)", "Notes App Pro (annual plan)"),
            value_5=entry("AppMarket", "Payment to AppMarket"),
            value_6=entry("PayRelay", "Receipt from PayRelay"),
        )
    if "Subject: Receipt for your payment to HostHill" in text:
        return reply(
            value_1=entry(4.5, "Amount: 4.50 EUR"),
            value_2=entry("2026-11-04", "Date: 2026-11-04"),
        )
    if "Subject: Чек Streamly" in text:
        return reply(
            value_1=entry(399.0, "399,00 ₽ в месяц"),
            value_2=entry("2026-11-12", "12 ноября 2026"),
            value_3=entry("Yes", "Следующее списание: 12 декабря 2026"),
        )
    return reply()


# Road distances are offered only with a routing key. The injected transport
# answers every request itself, so this value never leaves the process.
os.environ.setdefault("OPENROUTESERVICE_API_KEY", "offline-fixture")
# The usage summary opens with this code in the browser scenarios; it guards
# nothing but counters of this test process.
USAGE_CODE = "fixture-admin-code"
# The universal search is switched on here with a stand-in for the model client,
# made inside the run worker: no request leaves the process, and the key below
# is only what the stand-in expects to be handed.
LOOKUP_KEY = "offline-fixture-key"
os.environ.setdefault("ENRICHMENT_ALLOW_WEB_LOOKUP", "1")
os.environ.setdefault("OPENAI_API_KEY", LOOKUP_KEY)
LOOKUP_DIRECTORY = Path(tempfile.mkdtemp(prefix="enrichment-lookup-"))
atexit.register(shutil.rmtree, LOOKUP_DIRECTORY, ignore_errors=True)
LOOKUP_LOG = LOOKUP_DIRECTORY / "requests.jsonl"
app = create_app(
    offline_plan_client=SamplePlanClient(),
    offline_executor_factory=offline_executor,
    offline_lookup_api=LoggedApiFactory(
        os.environ["OPENAI_API_KEY"], LOOKUP_LOG, answer=lookup_answer
    ),
    admin_code=USAGE_CODE,
)

# A second app, for the e-mail scenario (frontend/e2e/mail.spec.ts), served on its own port
# next to the one above, which keeps the mail entry off like a hosted installation. It differs
# from `app` only in three things: mail is switched on (`allow_mail`, and
# `local_browser_configuration`, which a hosted deployment never has), the run worker gets a
# stand-in for the model client that reads values out of texts (`offline_extraction_api`, made
# inside the worker like the search's), and it has no plan client, because a table of mails is
# never planned by the model. The stand-in expects the same key as above and logs each request.
EXTRACTION_LOG = LOOKUP_DIRECTORY / "extraction-requests.jsonl"
mail_app = create_app(
    settings=dataclasses.replace(PlanRunSettings.from_environment(), allow_mail=True),
    local_browser_configuration=True,
    offline_executor_factory=offline_executor,
    offline_extraction_api=LoggedExtractionApiFactory(
        os.environ["OPENAI_API_KEY"], EXTRACTION_LOG, answer=mail_answer
    ),
)
