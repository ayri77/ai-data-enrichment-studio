"""Bundled example workbooks a visitor can try without a file of their own.

Each example pairs a public sample workbook with the instruction that was
measured in repeated live planning runs and with a prepared plan, so a demo can
proceed even when the planning provider is unavailable. The files are read
from the working directory (repository root locally, `/app` in the image); an
example whose workbook is missing is simply not offered.
"""

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    sample_id: str
    workbook: str
    plan: str
    instruction: str
    # Each column the example fills: its header and its letter on the sheet.
    outputs: tuple[tuple[str, str], ...]
    # Capabilities the example needs beyond the built-in ones; it is offered
    # only where all of them are.
    requires: frozenset[str] = frozenset()


SAMPLES = (
    Sample(
        sample_id="capitals",
        workbook="samples/capitals.xlsx",
        plan="demo/capitals_api_plan.json",
        instruction=(
            "Calculate the great-circle distance in kilometers between each pair of cities in "
            "Capital_From and Capital_To. Use Country_From and Country_To to disambiguate "
            "locations. Treat all locations as literal cities, including Istanbul. Write verified "
            "distances into the distance output column. Leave unresolved or ambiguous results "
            "empty; do not invent values."
        ),
        outputs=(("distance", "E"),),
    ),
    Sample(
        sample_id="mountains",
        workbook="samples/mountains.xlsx",
        plan="demo/mountains_api_plan.json",
        instruction=(
            "Find the elevation in metres of each mountain in Mountain, using Country to "
            "disambiguate. Write verified values into height. Leave unresolved or ambiguous "
            "results empty; do not invent values."
        ),
        outputs=(("height", "C"),),
    ),
    # A plan that branches and fills three columns. The instruction is the one
    # a live planning run turned into exactly the prepared plan (2026-11-13).
    Sample(
        sample_id="capitals_roads",
        workbook="samples/capitals_roads.xlsx",
        plan="demo/capitals_roads_api_plan.json",
        instruction=(
            "For each pair of cities in Capital_From and Capital_To calculate three values: "
            "the great-circle distance in kilometers into distance, the driving distance by "
            "road in kilometers into road, and how many times the road distance is longer "
            "than the great-circle distance into detour. Use Country_From and Country_To to "
            "disambiguate locations. Treat all locations as literal cities, including "
            "Istanbul. Leave unresolved or ambiguous results empty; do not invent values."
        ),
        outputs=(("distance", "E"), ("road", "F"), ("detour", "G")),
        requires=frozenset({"road_distance"}),
    ),
)


def _root() -> Path:
    return Path(os.getenv("ENRICHMENT_DATA_DIR", "."))


def workbook_path(sample: Sample) -> Path | None:
    path = _root() / sample.workbook
    return path if path.is_file() else None


def prepared_plan(sample: Sample) -> dict | None:
    path = _root() / sample.plan
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def find(sample_id: str) -> Sample | None:
    return next((s for s in SAMPLES if s.sample_id == sample_id), None)


def available(offered: Iterable[str] = ()) -> list[dict]:
    """Public description of every example that can be run here.

    ``offered`` are the ids of the capabilities this installation offers. An
    example whose workbook is missing, or that needs a capability which is not
    offered, is left out rather than shown and then refused.
    """
    capabilities = frozenset(offered)
    return [
        {
            "sample_id": sample.sample_id,
            "filename": Path(sample.workbook).name,
            "instruction": sample.instruction,
            "outputs": [{"name": name, "column": column} for name, column in sample.outputs],
            "has_plan": (_root() / sample.plan).is_file(),
        }
        for sample in SAMPLES
        if workbook_path(sample) is not None and sample.requires <= capabilities
    ]
