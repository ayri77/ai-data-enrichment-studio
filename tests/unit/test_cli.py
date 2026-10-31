"""CLI smoke tests."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from openai import OpenAIError
from openpyxl import Workbook
from typer.testing import CliRunner

from ai_data_enrichment.cli import app
from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.planning.llm_planner import PlanGenerationError
from ai_data_enrichment.planning.openai_client import OpenAICompletionError, OpenAIPlanClient

runner = CliRunner()


def test_version_command() -> None:
    """Version command exits successfully and prints the package version."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_inspect_reports_missing_file() -> None:
    """Invalid file paths produce a controlled error."""
    result = runner.invoke(app, ["inspect", "missing.xlsx"])
    assert result.exit_code == 2
    assert "Workbook not found" in result.output


def test_plan_validate_accepts_valid_saved_plan(tmp_path: Path) -> None:
    """Validate a saved enrichment plan against a dataset profile."""

    profile = DatasetProfile(
        dataset_id="test",
        name="Test dataset",
        columns=[],
    )

    plan = {
        "version": "1.0",
        "steps": [
            {
                "id": "resolve",
                "capability": "resolve_entity",
                "inputs": {
                    "name": {"kind": "literal", "value": "Everest"},
                    "country": {"kind": "literal", "value": "Nepal"},
                },
                "parameters": {"entity_type": "mountain"},
            },
            {
                "id": "elevation",
                "capability": "lookup_property",
                "inputs": {
                    "entity": {
                        "kind": "step_output",
                        "step_id": "resolve",
                    }
                },
                "parameters": {"property": "elevation"},
            },
        ],
        "outputs": [
            {
                "step_id": "elevation",
                "value_field": "value",
                "target_column": "height",
            }
        ],
        "assumptions": [],
    }

    plan_path = tmp_path / "plan.json"
    profile_path = tmp_path / "profile.json"

    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    profile_path.write_text(
        profile.model_dump_json(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "plan",
            "validate",
            str(plan_path),
            "--profile",
            str(profile_path),
        ],
    )

    assert result.exit_code == 0, result.output

    response = json.loads(result.stdout)

    assert response == {
        "valid": True,
        "errors": [],
    }

    # The saved plan is valid but does not satisfy the Mountains scenario:
    # it uses literal values instead of the required source columns.
    evaluation = runner.invoke(
        app,
        [
            "plan",
            "evaluate",
            str(plan_path),
            "--profile",
            str(profile_path),
            "--scenario",
            "mountains",
        ],
    )

    assert evaluation.exit_code == 1, evaluation.output

    report = json.loads(evaluation.stdout)

    assert report["validation"] == {
        "errors": [],
        "valid": True,
    }

    assert report["scenario"]["correct"] is False
    assert any("Mountain" in error for error in report["scenario"]["errors"])


def test_plan_validate_rejects_empty_plan(tmp_path: Path) -> None:
    """Return structured diagnostics and exit code 1 for an invalid plan."""

    plan = {
        "version": "1.0",
        "steps": [],
        "outputs": [],
        "assumptions": [],
    }

    profile = DatasetProfile(
        dataset_id="test",
        name="Test dataset",
        columns=[],
    )

    plan_path = tmp_path / "plan.json"
    profile_path = tmp_path / "profile.json"

    plan_path.write_text(
        json.dumps(plan),
        encoding="utf-8",
    )
    profile_path.write_text(
        profile.model_dump_json(),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "plan",
            "validate",
            str(plan_path),
            "--profile",
            str(profile_path),
        ],
    )

    assert result.exit_code == 1

    response = json.loads(result.stdout)

    assert response["valid"] is False
    assert {error["code"] for error in response["errors"]} == {
        "EMPTY_PLAN_STEPS",
        "EMPTY_PLAN_OUTPUTS",
    }


@pytest.mark.parametrize(
    "plan_text",
    [
        None,
        "{invalid json",
        '{"version": "1.0"}',
    ],
)
def test_plan_validate_rejects_invalid_input(
    tmp_path: Path,
    plan_text: str | None,
) -> None:
    """Return exit code 2 for missing or malformed plan input."""

    plan_path = tmp_path / "plan.json"
    profile_path = tmp_path / "profile.json"

    profile = DatasetProfile(
        dataset_id="test",
        name="Test dataset",
        columns=[],
    )

    profile_path.write_text(
        profile.model_dump_json(),
        encoding="utf-8",
    )

    if plan_text is not None:
        plan_path.write_text(plan_text, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "plan",
            "validate",
            str(plan_path),
            "--profile",
            str(profile_path),
        ],
    )

    assert result.exit_code == 2
    assert "Error:" in result.output


def test_plan_generate_saves_valid_plan_from_workbook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generate and save a validated plan from an Excel workbook."""

    workbook_path = tmp_path / "mountains.xlsx"
    output_path = tmp_path / "plan.json"
    profile_path = tmp_path / "profile.json"

    workbook = Workbook()
    try:
        sheet = workbook.active
        assert sheet is not None

        sheet.title = "Mountains"
        sheet.append(["Mountain", "Country", "height"])
        sheet.append(["Everest", "Nepal", None])

        workbook.save(workbook_path)
    finally:
        workbook.close()

    planning_response = {
        "status": "ready",
        "plan": {
            "version": "1.0",
            "steps": [
                {
                    "id": "resolve",
                    "capability": "resolve_entity",
                    "inputs": {
                        "name": {"kind": "column", "name": "Mountain"},
                        "country": {"kind": "column", "name": "Country"},
                    },
                    "parameters": {"entity_type": "mountain"},
                },
                {
                    "id": "elevation",
                    "capability": "lookup_property",
                    "inputs": {
                        "entity": {
                            "kind": "step_output",
                            "step_id": "resolve",
                        },
                    },
                    "parameters": {"property": "elevation"},
                },
            ],
            "outputs": [
                {
                    "step_id": "elevation",
                    "value_field": "value",
                    "target_column": "height",
                }
            ],
            "assumptions": [],
        },
        "reason": None,
    }

    # Ensure the test cannot make a real API request.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with patch.object(
        OpenAIPlanClient,
        "complete",
        return_value=json.dumps(planning_response),
    ) as mock_complete:
        result = runner.invoke(
            app,
            [
                "plan",
                "generate",
                str(workbook_path),
                "--sheet",
                "Mountains",
                "--instruction",
                "Find the elevation of each mountain.",
                "--output",
                str(output_path),
                "--profile-output",
                str(profile_path),
            ],
        )

    assert result.exit_code == 0, result.output
    assert output_path.is_file()

    saved_plan = EnrichmentPlan.model_validate_json(output_path.read_text(encoding="utf-8"))

    assert [step.capability for step in saved_plan.steps] == [
        "resolve_entity",
        "lookup_property",
    ]
    assert saved_plan.outputs[0].target_column == "height"

    mock_complete.assert_called_once()

    # Verify the dataset profile was exported and can be restored.
    assert profile_path.is_file()

    saved_profile = DatasetProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))

    assert saved_profile.name == "Mountains"
    assert saved_profile.row_count == 1
    assert [column.name for column in saved_profile.columns] == [
        "Mountain",
        "Country",
        "height",
    ]

    # Verify that saved artifacts work together without calling the LLM.
    validation_result = runner.invoke(
        app,
        [
            "plan",
            "validate",
            str(output_path),
            "--profile",
            str(profile_path),
        ],
    )

    assert validation_result.exit_code == 0, validation_result.output
    assert json.loads(validation_result.stdout)["valid"] is True

    # The generated plan must also satisfy the Mountains scenario.
    evaluation_result = runner.invoke(
        app,
        [
            "plan",
            "evaluate",
            str(output_path),
            "--profile",
            str(profile_path),
            "--scenario",
            "mountains",
        ],
    )

    assert evaluation_result.exit_code == 0, evaluation_result.output

    report = json.loads(evaluation_result.stdout)

    assert report["validation"]["valid"] is True
    assert report["validation"]["errors"] == []
    assert report["scenario"] == {
        "correct": True,
        "errors": [],
    }


@pytest.mark.parametrize(
    ("decision", "expected_message"),
    [
        (
            {
                "status": "unsupported",
                "plan": None,
                "reason": "Requested operation is unavailable.",
            },
            "Planning unsupported",
        ),
        (
            {
                "status": "ambiguous",
                "plan": None,
                "reason": "Input columns are ambiguous.",
            },
            "Planning ambiguous",
        ),
        (
            {
                "status": "ready",
                "plan": {
                    "version": "1.0",
                    "steps": [],
                    "outputs": [],
                    "assumptions": [],
                },
                "reason": None,
            },
            "Generated plan failed validation",
        ),
    ],
)
def test_plan_generate_does_not_save_invalid_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision: dict[str, object],
    expected_message: str,
) -> None:
    """Reject failed planning decisions without writing an output file."""

    output_path = tmp_path / "plan.json"

    profile = DatasetProfile(
        dataset_id="test",
        name="Test dataset",
        columns=[],
    )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with (
        patch(
            "ai_data_enrichment.cli.profile_worksheet",
            return_value=profile,
        ),
        patch.object(
            OpenAIPlanClient,
            "complete",
            return_value=json.dumps(decision),
        ) as mock_complete,
    ):
        result = runner.invoke(
            app,
            [
                "plan",
                "generate",
                str(tmp_path / "input.xlsx"),
                "--sheet",
                "Sheet1",
                "--instruction",
                "Enrich the dataset.",
                "--output",
                str(output_path),
            ],
        )

    assert result.exit_code == 1, result.output
    assert expected_message in result.output
    assert not output_path.exists()

    mock_complete.assert_called_once()


@pytest.mark.parametrize(
    "failure",
    [
        OpenAIError("Synthetic API failure"),
        OpenAICompletionError("Response incomplete"),
        PlanGenerationError("Invalid planning response"),
    ],
)
def test_plan_generate_handles_llm_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    """Report planning failures without writing an output file."""

    output_path = tmp_path / "plan.json"

    profile = DatasetProfile(
        dataset_id="test",
        name="Test dataset",
        columns=[],
    )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with (
        patch(
            "ai_data_enrichment.cli.profile_worksheet",
            return_value=profile,
        ),
        patch.object(
            OpenAIPlanClient,
            "complete",
            side_effect=failure,
        ) as mock_complete,
    ):
        result = runner.invoke(
            app,
            [
                "plan",
                "generate",
                str(tmp_path / "input.xlsx"),
                "--sheet",
                "Sheet1",
                "--instruction",
                "Enrich the dataset.",
                "--output",
                str(output_path),
            ],
        )

    assert result.exit_code == 2
    assert "Error:" in result.output
    assert not output_path.exists()
    mock_complete.assert_called_once()


def test_plan_evaluate_rejects_unknown_scenario() -> None:
    """Reject unsupported evaluation scenarios with exit code 2."""

    result = runner.invoke(
        app,
        [
            "plan",
            "evaluate",
            "plan.json",
            "--profile",
            "profile.json",
            "--scenario",
            "unknown",
        ],
    )

    assert result.exit_code == 2
    assert "Unsupported scenario: unknown" in result.output


@pytest.mark.parametrize(
    "collision",
    [
        "plan_overwrites_input",
        "profile_overwrites_input",
        "outputs_share_path",
    ],
)
def test_plan_generate_rejects_unsafe_output_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collision: str,
) -> None:
    """Reject colliding paths before calling the LLM."""

    workbook_path = tmp_path / "source.xlsx"
    original_content = b"Original workbook must remain unchanged"
    workbook_path.write_bytes(original_content)

    plan_path = tmp_path / "plan.json"
    profile_path = tmp_path / "profile.json"

    if collision == "plan_overwrites_input":
        plan_path = workbook_path
    elif collision == "profile_overwrites_input":
        profile_path = workbook_path
    else:
        profile_path = plan_path

    dataset_profile = DatasetProfile(
        dataset_id="test",
        name="Test",
        columns=[],
    )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with (
        patch(
            "ai_data_enrichment.cli.profile_worksheet",
            return_value=dataset_profile,
        ),
        patch.object(
            OpenAIPlanClient,
            "complete",
            side_effect=AssertionError("LLM must not be called"),
        ),
    ):
        result = runner.invoke(
            app,
            [
                "plan",
                "generate",
                str(workbook_path),
                "--sheet",
                "Sheet1",
                "--instruction",
                "Enrich the dataset.",
                "--output",
                str(plan_path),
                "--profile-output",
                str(profile_path),
            ],
        )

    assert result.exit_code == 2, result.output
    assert workbook_path.read_bytes() == original_content
    assert not (tmp_path / "plan.json").exists()
    assert not (tmp_path / "profile.json").exists()
