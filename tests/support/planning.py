"""Public workbook cases and deterministic completions, never evidence of model quality."""

import json
from pathlib import Path
from typing import Any

from ai_data_enrichment import sample_catalog
from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.ingestion.table_selection import TableSelection

CAPITALS_INSTRUCTION = (
    "Calculate the great-circle distance in kilometers between each pair of cities in "
    "Capital_From and Capital_To. Use Country_From and Country_To to disambiguate locations. "
    "Treat all locations as literal cities, including Istanbul. Write verified distances "
    "into the distance output column. Leave unresolved or ambiguous results empty; "
    "do not invent values."
)
MOUNTAINS_INSTRUCTION = (
    "Find the elevation in metres of each mountain in Mountain, using Country to "
    "disambiguate. Write verified values into height. Leave unresolved or ambiguous "
    "results empty; do not invent values."
)
# The partly filled table is measured with the instruction a visitor gets, too.
_GAPS = sample_catalog.find("mountains_gaps")
assert _GAPS is not None
MOUNTAINS_GAPS_INSTRUCTION = _GAPS.instruction
# The three-column example is measured with the instruction a visitor gets.
_ROADS = sample_catalog.find("capitals_roads")
assert _ROADS is not None
CAPITALS_ROADS_INSTRUCTION = _ROADS.instruction
RANGES = {
    "capitals": "A1:E11",
    "mountains": "A1:C11",
    "mountains_gaps": "A1:C11",
    "capitals_roads": "A1:G11",
}


def workbook_profile(name: str) -> DatasetProfile:
    return profile_worksheet(
        Path(f"samples/{name}.xlsx"),
        "Sheet1",
        name,
        selection=TableSelection("Sheet1", RANGES[name]),
    )


def ready_response(name: str = "capitals") -> dict[str, Any]:
    return {
        "status": "ready",
        "plan": json.loads(Path(f"demo/{name}_api_plan.json").read_text("utf-8")),
        "reason": None,
    }


def invalid_unit_response() -> dict[str, Any]:
    response = ready_response()
    response["plan"]["steps"][-1]["parameters"]["unit"] = {"kind": "literal", "value": "km"}
    return response


class ScriptedPlanClient:
    """Exercise the real planner with successive raw responses or completion failures."""

    def __init__(self, *responses: str | dict[str, Any] | Exception) -> None:
        self.responses = responses
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []

    def complete(self, *, messages: list[dict[str, str]], response_schema: dict[str, Any]) -> str:
        self.calls.append((messages, response_schema))
        response = self.responses[len(self.calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response)
