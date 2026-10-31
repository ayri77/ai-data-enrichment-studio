"""Tests for independent planning scenario evaluation."""

import pytest

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import (
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
)
from ai_data_enrichment.evaluation.planning import (
    evaluate_capitals_plan,
    evaluate_mountains_plan,
    evaluate_planning_case,
)
from ai_data_enrichment.validation.plan_validator import PlanValidator


def test_capitals_evaluation_rejects_wrong_destination() -> None:
    """Detect a structurally valid plan using the wrong destination columns."""

    profile = DatasetProfile(
        dataset_id="capitals",
        name="Capitals",
        columns=[
            ColumnProfile(
                name=name,
                inferred_type=(PrimitiveType.NUMBER if name == "distance" else PrimitiveType.TEXT),
                null_count=0,
            )
            for name in [
                "Capital_From",
                "Country_From",
                "Capital_To",
                "Country_To",
                "distance",
            ]
        ],
    )

    plan = EnrichmentPlan.model_validate(
        {
            "version": "1.0",
            "steps": [
                {
                    "id": "origin",
                    "capability": "geocode",
                    "inputs": {
                        "city": {
                            "kind": "column",
                            "name": "Capital_From",
                        },
                        "country": {
                            "kind": "column",
                            "name": "Country_From",
                        },
                    },
                    "parameters": {},
                },
                {
                    "id": "destination",
                    "capability": "geocode",
                    "inputs": {
                        "city": {
                            "kind": "column",
                            "name": "Capital_From",
                        },
                        "country": {
                            "kind": "column",
                            "name": "Country_From",
                        },
                    },
                    "parameters": {},
                },
                {
                    "id": "distance",
                    "capability": "geodesic_distance",
                    "inputs": {
                        "point_a": {
                            "kind": "step_output",
                            "step_id": "origin",
                        },
                        "point_b": {
                            "kind": "step_output",
                            "step_id": "destination",
                        },
                    },
                    "parameters": {"unit": "km"},
                },
            ],
            "outputs": [
                {
                    "step_id": "distance",
                    "value_field": "value",
                    "target_column": "distance",
                }
            ],
            "assumptions": [],
        }
    )

    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)

    validation = PlanValidator(
        registry=registry,
        dataset_profile=profile,
    ).validate(plan)

    # The plan is structurally and semantically valid.
    assert validation.valid is True, validation.errors

    # But it does not fulfill the requested enrichment scenario.
    evaluation = evaluate_capitals_plan(plan)

    assert evaluation.correct is False
    assert any("Capital_To" in error for error in evaluation.errors)

    # Evaluate technical validity and scenario correctness together.
    report = evaluate_planning_case(
        plan=plan,
        dataset_profile=profile,
        registry=registry,
        scenario="capitals",
    )

    assert report.validation.valid is True
    assert report.validation.errors == []

    assert report.scenario.correct is False
    assert any("Capital_To" in error for error in report.scenario.errors)


@pytest.mark.parametrize(
    "extra_kind",
    ["none", "extra_step", "extra_output"],
)
def test_capitals_evaluation_checks_exact_scenario(extra_kind: str) -> None:
    """Accept a plan that correctly calculates capital distances."""

    plan = EnrichmentPlan.model_validate(
        {
            "version": "1.0",
            "steps": [
                {
                    "id": "from_location",
                    "capability": "geocode",
                    "inputs": {
                        "city": {
                            "kind": "column",
                            "name": "Capital_From",
                        },
                        "country": {
                            "kind": "column",
                            "name": "Country_From",
                        },
                    },
                    "parameters": {},
                },
                {
                    "id": "to_location",
                    "capability": "geocode",
                    "inputs": {
                        "city": {
                            "kind": "column",
                            "name": "Capital_To",
                        },
                        "country": {
                            "kind": "column",
                            "name": "Country_To",
                        },
                    },
                    "parameters": {},
                },
                {
                    "id": "calculate_distance",
                    "capability": "geodesic_distance",
                    "inputs": {
                        "point_a": {
                            "kind": "step_output",
                            "step_id": "from_location",
                        },
                        "point_b": {
                            "kind": "step_output",
                            "step_id": "to_location",
                        },
                    },
                    "parameters": {"unit": "km"},
                },
            ],
            "outputs": [
                {
                    "step_id": "calculate_distance",
                    "value_field": "value",
                    "target_column": "distance",
                }
            ],
            "assumptions": [],
        }
    )

    if extra_kind == "extra_step":
        plan.steps.append(
            PlanStep.model_validate(
                {
                    "id": "unused_geocode",
                    "capability": "geocode",
                    "inputs": {
                        "city": {
                            "kind": "column",
                            "name": "Capital_From",
                        },
                        "country": {
                            "kind": "column",
                            "name": "Country_From",
                        },
                    },
                    "parameters": {},
                }
            )
        )

    elif extra_kind == "extra_output":
        plan.outputs.append(
            OutputMapping(
                step_id="calculate_distance",
                value_field="value",
                target_column="distance_copy",
            )
        )

    evaluation = evaluate_capitals_plan(plan)

    if extra_kind == "none":
        assert evaluation.correct is True
        assert evaluation.errors == []
    else:
        assert evaluation.correct is False
        assert evaluation.errors


@pytest.mark.parametrize(
    ("name_column", "extra_kind", "expected_correct"),
    [
        ("Mountain", "none", True),
        ("Country", "none", False),
        ("Mountain", "extra_step", False),
        ("Mountain", "extra_output", False),
    ],
)
def test_mountains_scenario_evaluation(
    name_column: str,
    extra_kind: str,
    expected_correct: bool,
) -> None:
    """Evaluate mountain plans independently of structural validation."""

    profile = DatasetProfile(
        dataset_id="mountains",
        name="Mountains",
        columns=[
            ColumnProfile(
                name="Mountain",
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
            ),
            ColumnProfile(
                name="Country",
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
            ),
            ColumnProfile(
                name="height",
                inferred_type=PrimitiveType.NUMBER,
                null_count=0,
            ),
        ],
    )

    plan = EnrichmentPlan.model_validate(
        {
            "version": "1.0",
            "steps": [
                {
                    "id": "resolve_mountain",
                    "capability": "resolve_entity",
                    "inputs": {
                        "name": {
                            "kind": "column",
                            "name": name_column,
                        },
                        "country": {
                            "kind": "column",
                            "name": "Country",
                        },
                    },
                    "parameters": {
                        "entity_type": "mountain",
                    },
                },
                {
                    "id": "get_elevation",
                    "capability": "lookup_property",
                    "inputs": {
                        "entity": {
                            "kind": "step_output",
                            "step_id": "resolve_mountain",
                        },
                    },
                    "parameters": {
                        "property": "elevation",
                    },
                },
            ],
            "outputs": [
                {
                    "step_id": "get_elevation",
                    "value_field": "value",
                    "target_column": "height",
                }
            ],
            "assumptions": [],
        }
    )

    if extra_kind == "extra_step":
        plan.steps.append(
            PlanStep.model_validate(
                {
                    "id": "unused_resolution",
                    "capability": "resolve_entity",
                    "inputs": {
                        "name": {
                            "kind": "column",
                            "name": "Mountain",
                        },
                        "country": {
                            "kind": "column",
                            "name": "Country",
                        },
                    },
                    "parameters": {
                        "entity_type": "mountain",
                    },
                }
            )
        )

    elif extra_kind == "extra_output":
        plan.outputs.append(
            OutputMapping(
                step_id="get_elevation",
                value_field="value",
                target_column="height_copy",
            )
        )

    registry = CapabilityRegistry()

    for capability in get_builtin_capabilities():
        registry.register(capability)

    validation = PlanValidator(
        registry=registry,
        dataset_profile=profile,
    ).validate(plan)

    # Both plans satisfy the generic structural and semantic contracts.
    assert validation.valid is True, validation.errors

    # Only the plan using Mountain as the entity name is scenario-correct.
    evaluation = evaluate_mountains_plan(plan)

    assert evaluation.correct is expected_correct

    if expected_correct:
        assert evaluation.errors == []
    elif name_column == "Country":
        assert any("Mountain" in error for error in evaluation.errors)
    else:
        assert evaluation.errors
