"""Requested output targets reach the planner and are validated inside the repair loop."""

import json
from typing import Any

import pytest
from tests.support.planning import (
    CAPITALS_INSTRUCTION,
    ScriptedPlanClient,
    ready_response,
    workbook_profile,
)

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.plan_run_worker import builtin_registry
from ai_data_enrichment.planning.llm_planner import LLMPlanner
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy, PlanValidator


def renamed_target_response(name: str) -> dict[str, Any]:
    response = ready_response()
    response["plan"]["outputs"][0]["target_column"] = name
    return response


def validate(policy: PlanValidationPolicy | None, target: str = "distance"):
    plan = EnrichmentPlan.model_validate(renamed_target_response(target)["plan"])
    return PlanValidator(builtin_registry(), workbook_profile("capitals"), policy).validate(plan)


@pytest.mark.parametrize(
    "requested,target,missing,unexpected",
    [
        ({"distance"}, "Distance_km", ["distance"], ["Distance_km"]),
        ({"distance", "duration"}, "distance", ["duration"], []),
    ],
)
def test_validator_reports_one_precise_target_mismatch(
    requested: set[str], target: str, missing: list[str], unexpected: list[str]
) -> None:
    result = validate(PlanValidationPolicy(output_targets=frozenset(requested)), target)
    assert not result.valid and not result.contract_valid
    mismatch = [e for e in result.errors if e.code == "OUTPUT_TARGET_MISMATCH"]
    assert len(mismatch) == 1 and mismatch[0].field == "outputs"
    assert f"Missing: {missing}." in mismatch[0].message
    assert f"Unexpected: {unexpected}." in mismatch[0].message


def test_matching_or_unrequested_targets_stay_valid() -> None:
    assert validate(PlanValidationPolicy(output_targets=frozenset({"distance"}))).valid
    # Without requested targets (CLI planning) the plan still names targets freely.
    assert validate(PlanValidationPolicy(), "Distance_km").valid
    assert validate(None, "Distance_km").valid


def generate(client: ScriptedPlanClient, policy: PlanValidationPolicy | None):
    return LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=CAPITALS_INSTRUCTION,
        dataset_profile=workbook_profile("capitals"),
        validation_policy=policy,
    )


def test_requested_targets_are_disclosed_to_the_model() -> None:
    client = ScriptedPlanClient(ready_response())
    generate(client, PlanValidationPolicy(output_targets=frozenset({"zeta", "distance"})))
    system, user = client.calls[0][0][:2]
    assert "If the context lists output_targets" in system["content"]
    assert json.loads(user["content"])["output_targets"] == ["distance", "zeta"]


@pytest.mark.parametrize("policy", [None, PlanValidationPolicy(max_steps=12)])
def test_context_has_no_targets_unless_the_caller_requests_them(policy) -> None:
    client = ScriptedPlanClient(ready_response())
    result = generate(client, policy)
    assert result.validation and result.validation.valid
    assert "output_targets" not in json.loads(client.calls[0][0][1]["content"])


def test_target_mismatch_is_repaired_inside_the_loop() -> None:
    client = ScriptedPlanClient(renamed_target_response("Distance_km"), ready_response())
    result = generate(client, PlanValidationPolicy(output_targets=frozenset({"distance"})))
    assert result.validation and result.validation.valid
    assert [(a.outcome, a.diagnostic_codes) for a in result.attempts] == [
        ("invalid", ("OUTPUT_TARGET_MISMATCH",)),
        ("ready", ()),
    ]
    feedback = json.loads(client.calls[1][0][-1]["content"])["validation_feedback"]
    assert "['distance']" in feedback["errors"][0]["message"]


def test_caller_step_limit_is_enforced_inside_the_loop() -> None:
    client = ScriptedPlanClient(ready_response(), ready_response())
    result = generate(client, PlanValidationPolicy(max_steps=2))
    assert result.validation and not result.validation.valid
    assert result.attempts[0].diagnostic_codes == ("STEP_LIMIT_EXCEEDED",)
