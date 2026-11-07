"""Tests for explicit Excel output column mapping."""

import pytest

from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
)
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.output.excel_output_mapping import (
    build_output_header_writes,
)


def make_plan() -> EnrichmentPlan:
    return EnrichmentPlan(
        version="1",
        steps=[
            PlanStep(
                id="get_elevation",
                capability="lookup",
                inputs={},
                parameters={},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="get_elevation",
                value_field="value",
                target_column="elevation_m",
            ),
            OutputMapping(
                step_id="get_elevation",
                value_field="unit",
                target_column="elevation_unit",
            ),
        ],
        assumptions=[],
    )


def test_maps_headers_for_offset_table() -> None:
    writes = build_output_header_writes(
        make_plan(),
        TableSelection("Mountains", "B6:D20"),
        output_columns={
            "elevation_m": "E",
            "elevation_unit": "F",
        },
    )

    assert writes == {
        "E6": "elevation_m",
        "F6": "elevation_unit",
    }


def test_rejects_column_inside_source_table() -> None:
    with pytest.raises(
        ValueError,
        match="overlaps selected table",
    ):
        build_output_header_writes(
            make_plan(),
            TableSelection("Mountains", "B6:D20"),
            output_columns={
                "elevation_m": "D",
                "elevation_unit": "F",
            },
        )


def test_rejects_duplicate_physical_column() -> None:
    with pytest.raises(
        ValueError,
        match="Duplicate Excel output column",
    ):
        build_output_header_writes(
            make_plan(),
            TableSelection("Mountains", "B6:D20"),
            output_columns={
                "elevation_m": "E",
                "elevation_unit": "E",
            },
        )


def test_rejects_missing_output_binding() -> None:
    with pytest.raises(
        ValueError,
        match="must match plan output targets",
    ):
        build_output_header_writes(
            make_plan(),
            TableSelection("Mountains", "B6:D20"),
            output_columns={
                "elevation_m": "E",
            },
        )
