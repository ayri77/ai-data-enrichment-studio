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
from ai_data_enrichment.plan_run_worker import (
    MountainPolicy,
    ResumeSelections,
    Reuse,
    live_executor,
)
from ai_data_enrichment.providers.http_transport import JsonTransportError
from ai_data_enrichment.providers.open_meteo import GEOCODING_URL
from ai_data_enrichment.providers.openrouteservice import DIRECTIONS_URL
from ai_data_enrichment.providers.wikidata import WIKIDATA_API_URL

# Deliberately sparse. Kyiv is ambiguous; unknown cities stay unresolved.
# Warsaw is known under its Polish name only, so that giving another
# name during review can be exercised.
FIXTURE_CITIES = {
    "Warszawa": (756135, "PL", 52.22977, 21.01178),
    "London": (2643743, "GB", 51.50853, -0.12574),
    "Rome": (3169070, "IT", 41.89193, 12.51133),
    "Paris": (2988507, "FR", 48.85341, 2.3488),
    "Kyiv": (703448, "UA", 50.45466, 30.5238),
}
# Route lengths in metres between fixture cities, in either direction. Round
# figures of the right order; a pair that is not listed has no route.
FIXTURE_ROADS = {
    frozenset({"Paris", "London"}): 460_000.0,
    frozenset({"Rome", "Paris"}): 1_420_000.0,
    frozenset({"Rome", "London"}): 1_870_000.0,
    frozenset({"Warszawa", "Paris"}): 1_590_000.0,
    frozenset({"Warszawa", "London"}): 1_620_000.0,
    # Warszawa and Rome are left out on purpose: a pair without a route.
}
FIXTURE_GRAPH_DATE = "2026-11-02T00:00:00Z"


class APITransportFixture:
    def post_json(self, *, url: str, body: Mapping[str, object], timeout_seconds: float) -> object:
        """Answer a route request the way the routing service does."""
        if url != DIRECTIONS_URL:
            raise AssertionError("No external transport is allowed in this fixture.")
        by_point = {
            (longitude, latitude): city
            for city, (_, _, latitude, longitude) in FIXTURE_CITIES.items()
        }
        coordinates = body["coordinates"]
        assert isinstance(coordinates, list)
        ends = frozenset(by_point.get(tuple(point), "") for point in coordinates)
        metres = FIXTURE_ROADS.get(ends)
        if metres is None:
            raise JsonTransportError(
                "http_status",
                status_code=404,
                payload={"error": {"code": 2009, "message": "Route could not be found."}},
            )
        return {
            "routes": [{"summary": {"distance": metres, "duration": 0.0}}],
            "metadata": {"engine": {"graph_date": FIXTURE_GRAPH_DATE}},
        }

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        if url == WIKIDATA_API_URL:
            return OfflineWikidataTransport().get_json(
                url=url, params=params, timeout_seconds=timeout_seconds
            )
        if url != GEOCODING_URL:
            raise AssertionError("No external transport is allowed in this fixture.")
        locations = FIXTURE_CITIES
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
    reuse: Reuse | None = None,
) -> JobExecutor:
    """Top-level and picklable on Windows: same registered, audited live stack."""
    return live_executor(
        capabilities,
        policy,
        max_rows,
        transport=APITransportFixture(),
        resume_selections=resume_selections,
        reuse=reuse,
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


class FixtureTasksClient:
    """Answer a request for task ideas from the table's column names."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, *, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> str:
        self.calls.append({"messages": messages, "response_schema": response_schema})
        table = json.loads(messages[1]["content"])
        columns = [column["name"] for column in table["columns"]]
        empty = [c["name"] for c in table["columns"] if c["empty_cells"] >= (table["rows"] or 0)]
        if table.get("fill_columns"):
            # Several columns were chosen: one task that names every one of them.
            chosen = ", ".join(table["fill_columns"])
            return json.dumps(
                {
                    "tasks": [
                        f"[{table['language']}] Fill {chosen} for {columns[0]} and {columns[2]}.",
                        "An alternative the caller must not keep.",
                    ]
                }
            )
        if len(columns) >= 5 and empty:
            task = f"Calculate the distance in km between {columns[0]} and {columns[2]}"
            return json.dumps(
                {"tasks": [f"[{table['language']}] {task} and write it to {empty[0]}."]}
            )
        if empty:
            task = f"Find the elevation in metres of each {columns[0]}"
            return json.dumps(
                {"tasks": [f"[{table['language']}] {task} and write it to {empty[0]}."]}
            )
        return json.dumps({"tasks": []})


def asks_for_tasks(response_schema: dict[str, Any]) -> bool:
    """Whether a completion request is for task ideas rather than a plan."""
    return "tasks" in response_schema.get("properties", {})


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
