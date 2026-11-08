"""Offline end-to-end Capitals test through the real CLI and Excel writer."""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from typer.testing import CliRunner

from ai_data_enrichment.cli import app
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.providers.http_transport import UrllibJsonTransport
from ai_data_enrichment.resolution.models import ResolutionJobReport


def test_capitals_excel_pipeline_preserves_workbook_and_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve two capitals, calculate distance, and retain audit evidence."""

    requests: list[dict[str, str]] = []

    locations = {
        "Berlin": [
            {
                "id": 2950159,
                "name": "Berlin",
                "country_code": "DE",
                "feature_code": "PPLC",
                "latitude": 52.52437,
                "longitude": 13.41053,
            },
            {
                "id": 2950158,
                "name": "Berlin",
                "country_code": "DE",
                "feature_code": "PPL",
                "latitude": 54.033,
                "longitude": 10.45,
            },
        ],
        "Kyiv": [
            {
                "id": 703448,
                "name": "Kyiv",
                "country_code": "UA",
                "feature_code": "PPLC",
                "latitude": 50.45466,
                "longitude": 30.5238,
            },
            {
                "id": 704213,
                "name": "Kyiv",
                "country_code": "UA",
                "feature_code": "PPL",
                "latitude": 47.0,
                "longitude": 32.0,
            },
        ],
    }

    def fake_get_json(
        self: UrllibJsonTransport,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        assert url == "https://geocoding-api.open-meteo.com/v1/search"
        assert timeout_seconds == 10.0
        assert params["count"] == "100"

        requests.append(dict(params))
        return {"results": locations[params["name"]]}

    monkeypatch.setattr(UrllibJsonTransport, "get_json", fake_get_json)

    source = tmp_path / "capitals.xlsx"
    output = tmp_path / "capitals_enriched.xlsx"
    plan_file = tmp_path / "plan.json"
    columns_file = tmp_path / "columns.json"

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Capitals"

    sheet.append(["City A", "Country A", "City B", "Country B"])
    sheet.append(["Berlin", "Germany", "Kyiv", "Ukraine"])
    sheet["F2"] = "=1+2"

    notes = workbook.create_sheet("Notes")
    notes["A1"] = "Preserve this worksheet"

    workbook.save(source)
    workbook.close()

    source_bytes = source.read_bytes()

    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="capital_a",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="City A"),
                    "country": ColumnReference(kind="column", name="Country A"),
                },
                parameters={"required_role": "country_capital"},
            ),
            PlanStep(
                id="capital_b",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="City B"),
                    "country": ColumnReference(kind="column", name="Country B"),
                },
                parameters={"required_role": "country_capital"},
            ),
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(kind="step_output", step_id="capital_a"),
                    "point_b": StepOutputReference(kind="step_output", step_id="capital_b"),
                },
                parameters={"unit": "km"},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="distance",
                value_field="value",
                target_column="distance_km",
                expected_unit="km",
            ),
        ],
        assumptions=[],
    )

    plan_file.write_text(plan.model_dump_json(), encoding="utf-8")
    columns_file.write_text(
        json.dumps({"distance_km": "E"}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            str(source),
            "--plan",
            str(plan_file),
            "--sheet",
            "Capitals",
            "--range",
            "A1:D2",
            "--columns-file",
            str(columns_file),
            "--output",
            str(output),
            "--allow-network",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Rows: 1, success: 1, partial: 0, failed: 0" in result.output

    assert len(requests) == 2
    assert {request["name"] for request in requests} == {
        "Berlin",
        "Kyiv",
    }

    assert source.read_bytes() == source_bytes

    enriched = load_workbook(output, data_only=False)
    try:
        sheet = enriched["Capitals"]

        assert sheet["E1"].value == "distance_km"

        distance = sheet["E2"].value
        assert isinstance(distance, (int, float))
        assert distance == pytest.approx(1203.9890563606518, abs=0.001)

        assert sheet["F2"].value == "=1+2"
        assert enriched["Notes"]["A1"].value == "Preserve this worksheet"
    finally:
        enriched.close()

    report_path = output.with_suffix(".resolution.json")
    assert report_path.exists()

    report = ResolutionJobReport.model_validate_json(report_path.read_text(encoding="utf-8"))

    assert report.status == "completed"
    assert len(report.operations) == 2

    selected_ids = {
        operation.report.decision.selected.entity_id
        for operation in report.operations
        if operation.report.decision.selected is not None
    }

    assert selected_ids == {"2950159", "703448"}

    assert all(operation.report.decision.status == "resolved" for operation in report.operations)

    assert {
        (link.record_id, link.step_id)
        for operation in report.operations
        for link in operation.record_links
    } == {
        ("Capitals!2", "capital_a"),
        ("Capitals!2", "capital_b"),
    }

    assert all(
        len(operation.report.candidate_set.candidates) == 2 for operation in report.operations
    )

    # Negative E2E: an explicit meter destination must not receive a
    # kilometer Quantity, even when the numeric calculation succeeds.
    wrong_unit_plan = plan.model_copy(
        update={
            "outputs": [
                plan.outputs[0].model_copy(update={"expected_unit": "m"}),
            ]
        }
    )
    plan_file.write_text(wrong_unit_plan.model_dump_json(), encoding="utf-8")
    incompatible_output = tmp_path / "capitals_wrong_units.xlsx"
    incompatible_run = CliRunner().invoke(
        app,
        [
            "run",
            str(source),
            "--plan",
            str(plan_file),
            "--sheet",
            "Capitals",
            "--range",
            "A1:D2",
            "--columns-file",
            str(columns_file),
            "--output",
            str(incompatible_output),
            "--allow-network",
        ],
    )
    assert incompatible_run.exit_code == 2
    assert "Output unit mismatch" in incompatible_run.output
    assert not incompatible_output.exists()
    assert source.read_bytes() == source_bytes

    # Restore the original kilometer contract for the next failure case.
    plan_file.write_text(plan.model_dump_json(), encoding="utf-8")

    # Negative E2E: a saturated Kyiv search must fail closed.
    locations["Kyiv"].extend(
        {
            "id": 9000 + i,
            "name": f"Other place {i}",
            "country_code": "UA",
            "latitude": 49.0,
            "longitude": 31.0,
        }
        for i in range(98)
    )

    assert len(locations["Kyiv"]) == 100

    blocked_output = tmp_path / "capitals_incomplete.xlsx"

    blocked_result = CliRunner().invoke(
        app,
        [
            "run",
            str(source),
            "--plan",
            str(plan_file),
            "--sheet",
            "Capitals",
            "--range",
            "A1:D2",
            "--columns-file",
            str(columns_file),
            "--output",
            str(blocked_output),
            "--allow-network",
        ],
    )

    assert blocked_result.exit_code == 0, blocked_result.output
    assert "INCOMPLETE_VERIFICATION" in blocked_result.output

    # A workbook is produced, but no unsupported distance is written.
    blocked_workbook = load_workbook(blocked_output)
    try:
        blocked_sheet = blocked_workbook["Capitals"]
        assert blocked_sheet["E1"].value == "distance_km"
        assert blocked_sheet["E2"].value is None
        assert blocked_sheet["F2"].value == "=1+2"
        assert blocked_workbook["Notes"]["A1"].value == "Preserve this worksheet"
    finally:
        blocked_workbook.close()

    blocked_report = ResolutionJobReport.model_validate_json(
        blocked_output.with_suffix(".resolution.json").read_text(encoding="utf-8")
    )

    assert blocked_report.status == "awaiting_review"
    assert len(blocked_report.operations) == 2

    kyiv_audit = next(
        operation
        for operation in blocked_report.operations
        if operation.report.candidate_set.query.name == "Kyiv"
    )

    assert kyiv_audit.report.candidate_set.search_complete is False
    assert kyiv_audit.report.decision.status == "incomplete_verification"
    assert kyiv_audit.report.decision.selected is None
    assert kyiv_audit.report.candidate_set.incomplete_reason == "result_limit_reached"

    # The source workbook remains untouched after both executions.
    assert source.read_bytes() == source_bytes
