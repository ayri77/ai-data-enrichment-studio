"""Independent factual smoke references for delivered M3 workbook artifacts.

These two curated examples are deliberately not a statistical accuracy study:
they detect gross unit/value mistakes and are separate from provider fixtures.
"""

import json
from pathlib import Path

import pytest
from openpyxl import load_workbook


def test_published_mountain_workbook_matches_external_reference() -> None:
    """Check the retained live-reviewed Everest artifact against a public value."""
    root = Path(__file__).resolve().parents[2]
    reference_file = root / "benchmarks/references/m3_external_smoke.json"
    reference = json.loads(reference_file.read_text(encoding="utf-8"))
    baseline = reference["references"]["everest"]

    # This is a checked-in demonstration artifact from a previous live run,
    # NOT a fresh network request or independent repeat of Wikidata discovery.
    workbook = load_workbook(
        root / "demo/mountains_resumed.xlsx",
        read_only=True,
        data_only=True,
    )
    try:
        sheet = workbook["Mountains"]
        assert sheet["C1"].value == "elevation_m"
        assert sheet["D1"].value == "elevation_unit"
        assert sheet["C2"].value == pytest.approx(
            baseline["expected_elevation_m"],
            abs=baseline["tolerance_m"],
        )
        assert sheet["D2"].value == "m"
    finally:
        workbook.close()
