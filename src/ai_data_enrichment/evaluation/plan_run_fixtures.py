"""Explicit offline API test/demo inputs; never enabled by the HTTP server.

The small HTTP fixtures intentionally leave most official records unresolved.
They exercise real provider parsing, verification, execution and XLSX writes;
they are not evidence of live coverage or independent academic correctness.
"""

import json
from collections.abc import Mapping
from typing import Any

from ai_data_enrichment.evaluation.smoke import OfflineWikidataTransport
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.plan_run_worker import MountainPolicy, ResumeSelections, live_executor
from ai_data_enrichment.providers.open_meteo import GEOCODING_URL
from ai_data_enrichment.providers.wikidata import WIKIDATA_API_URL


class APITransportFixture:
    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        if url == WIKIDATA_API_URL:
            return OfflineWikidataTransport().get_json(
                url=url, params=params, timeout_seconds=timeout_seconds
            )
        if url != GEOCODING_URL:
            raise AssertionError("No external transport is allowed in this fixture.")
        # Deliberately sparse. Kyiv is ambiguous; unknown cities stay unresolved.
        # Warsaw is known under its Polish name only, so that giving another
        # name during review can be exercised.
        locations = {
            "Warszawa": (756135, "PL", 52.22977, 21.01178),
            "London": (2643743, "GB", 51.50853, -0.12574),
            "Rome": (3169070, "IT", 41.89193, 12.51133),
            "Paris": (2988507, "FR", 48.85341, 2.3488),
            "Kyiv": (703448, "UA", 50.45466, 30.5238),
        }
        city = params["name"]
        if city not in locations:
            return {"results": []}
        identifier, country, lat, lon = locations[city]
        if params["countryCode"] != country:
            return {"results": []}
        candidate = {
            "id": identifier,
            "name": city,
            "country_code": country,
            "latitude": lat,
            "longitude": lon,
            "feature_code": "PPLC",
        }
        candidates = [candidate]
        if city == "Kyiv":
            candidates.append({**candidate, "id": 704213, "latitude": 47.0, "longitude": 32.0})
        return {"results": candidates}


def offline_executor(
    capabilities: frozenset[str],
    policy: MountainPolicy,
    max_rows: int,
    *,
    resume_selections: ResumeSelections | None = None,
) -> JobExecutor:
    """Top-level and picklable on Windows: same registered, audited live stack."""
    return live_executor(
        capabilities,
        policy,
        max_rows,
        transport=APITransportFixture(),
        resume_selections=resume_selections,
    )


class FixtureNamesClient:
    """Answer a request for other names of a place from a fixed table."""

    # Warsaw is the one name the offline source lists differently.
    NAMES = {"Warsaw": ["Warszawa", "Varsovie"], "Vienna": ["Wien"]}

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, *, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> str:
        self.calls.append({"messages": messages, "response_schema": response_schema})
        place = json.loads(messages[1]["content"])
        return json.dumps({"names": self.NAMES.get(place["name"], [])})


def asks_for_names(response_schema: dict[str, Any]) -> bool:
    """Whether a completion request is for other names rather than a plan."""
    return "names" in response_schema.get("properties", {})


class FixturePlanClient:
    """Return a supplied model response while exercising the real LLMPlanner."""

    def __init__(self, plan: dict | None, status: str = "ready") -> None:
        self.plan, self.status = plan, status
        self.calls: list[dict] = []

    def complete(self, *, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> str:
        self.calls.append({"messages": messages, "response_schema": response_schema})
        return json.dumps(
            {
                "status": self.status,
                "plan": self.plan,
                "reason": None if self.status == "ready" else "Fixture requires clarification.",
            }
        )
