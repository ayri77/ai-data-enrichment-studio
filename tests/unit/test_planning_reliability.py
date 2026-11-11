"""Offline #117 regressions: prompt contracts and validation/repair, not model quality."""

import json
from dataclasses import asdict
from typing import Any

import pytest
from tests.support.planning import (
    CAPITALS_INSTRUCTION,
    MOUNTAINS_INSTRUCTION,
    ScriptedPlanClient,
    invalid_unit_response,
    ready_response,
    workbook_profile,
)

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.plan_run_worker import builtin_registry
from ai_data_enrichment.planning.llm_planner import LLMPlanner, PlanGenerationError
from ai_data_enrichment.validation.plan_validator import PlanValidator


@pytest.mark.parametrize(
    "name,instruction", [("capitals", CAPITALS_INSTRUCTION), ("mountains", MOUNTAINS_INSTRUCTION)]
)
def test_free_text_workbook_composition_context(name: str, instruction: str) -> None:
    """Protect the guidance that was absent during the genuine incorrect no_plan."""
    client = ScriptedPlanClient(ready_response(name))
    profile = workbook_profile(name)
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=instruction, dataset_profile=profile
    )
    assert result.response.status == "ready"
    assert result.validation and result.validation.valid
    assert len(client.calls) == 1
    system = client.calls[0][0][0]["content"]
    for rule in (
        "Intermediate GeoPoint, EntityRef or Quantity values need not exist in source columns",
        "inventory compatible producers and consumers",
        "Before abstaining, check the full compatible capability chain",
        '"kind":"step_output","step_id":"producer_step"',
        "Parameters are direct JSON values",
        'parameters={"unit":"km"}',
        "does not impose an administrative role",
        "Do not invent source values",
    ):
        assert rule in system
    # No source workbook, column names or scenario DAG is baked into system instructions.
    assert "Capital_From" not in system and "Mountain" not in system
    context = json.loads(client.calls[0][0][1]["content"])
    assert context["instruction"] == instruction
    assert context["dataset_profile"] == profile.model_dump(mode="json")
    assert context["capabilities"] == builtin_registry().describe_all()
    assert len(context["dataset_profile"]["sample_records"]) <= 5
    assert result.response.plan
    if name == "capitals":
        geocodes = [s for s in result.response.plan.steps if s.capability == "geocode"]
        assert len(geocodes) == 2
        assert all("required_role" not in s.parameters for s in geocodes)
        assert {s.inputs["country"].model_dump()["name"] for s in geocodes} == {
            "Country_From",
            "Country_To",
        }


def test_object_unit_fails_independently_then_repairs_from_diagnostic() -> None:
    invalid = invalid_unit_response()
    profile = workbook_profile("capitals")
    validation = PlanValidator(builtin_registry(), profile).validate(
        EnrichmentPlan.model_validate(invalid["plan"])
    )
    assert not validation.valid
    assert [e.code for e in validation.errors] == ["PARAMETER_TYPE_MISMATCH"]
    client = ScriptedPlanClient(invalid, ready_response())
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=CAPITALS_INSTRUCTION, dataset_profile=profile
    )
    assert result.validation and result.validation.valid
    assert result.response.plan
    assert result.response.plan.steps[-1].parameters == {"unit": "km"}
    assert invalid["plan"]["steps"][-1]["parameters"]["unit"] == {
        "kind": "literal",
        "value": "km",
    }  # The planner did not silently coerce the proposal.
    assert len(client.calls) == 2
    assert result.attempts[0].diagnostic_codes == ("PARAMETER_TYPE_MISMATCH",)
    assert result.attempts[-1].outcome == "ready"
    repair_messages, schema = client.calls[1]
    assert schema == client.calls[0][1]
    assert repair_messages[:2] == client.calls[0][0]
    assert json.loads(repair_messages[2]["content"]) == invalid
    feedback = json.loads(repair_messages[3]["content"])["validation_feedback"]
    assert feedback["errors"] == [e.model_dump(mode="json") for e in validation.errors]


@pytest.mark.parametrize(
    "malformed,code",
    [
        ('{"status":', "INVALID_JSON"),
        ({"status": "ready", "plan": {"steps": "bad"}, "reason": None}, "INVALID_PLANNING_SCHEMA"),
        ({"status": "ready", "plan": None, "reason": None}, "INVALID_PLANNING_SCHEMA"),
    ],
)
def test_malformed_responses_receive_feedback_and_can_recover(
    malformed: str | dict[str, Any], code: str
) -> None:
    client = ScriptedPlanClient(malformed, ready_response())
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals")
    )
    assert result.validation and result.validation.valid
    assert len(client.calls) == 2
    assert result.attempts[0].diagnostic_codes == (code,)
    feedback = json.loads(client.calls[1][0][-1]["content"])["validation_feedback"]
    assert feedback["code"] == code
    assert "input" not in json.dumps(feedback)


def test_every_repair_is_revalidated_and_budget_counts_malformed_proposals() -> None:
    bad_reference = ready_response()
    bad_reference["plan"]["steps"][-1]["inputs"]["point_a"]["step_id"] = "missing_step"
    client = ScriptedPlanClient(invalid_unit_response(), bad_reference, '{"plan":')
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals")
    )
    assert len(client.calls) == 3
    assert result.validation and not result.validation.valid
    assert result.response.plan == EnrichmentPlan.model_validate(bad_reference["plan"])
    assert [a.number for a in result.attempts] == [1, 2, 3]
    assert result.attempts[0].diagnostic_codes == ("PARAMETER_TYPE_MISMATCH",)
    assert "MISSING_STEP_REFERENCE" in result.attempts[1].diagnostic_codes
    assert result.attempts[2].diagnostic_codes == ("INVALID_JSON",)
    assert len(client.calls[2][0]) == 4  # Only latest feedback, not an expanding history.
    assert json.loads(client.calls[2][0][2]["content"]) == bad_reference


@pytest.mark.parametrize("budget", [0, 1, 2])
def test_repeated_invalid_plan_remains_invalid_at_budget(budget: int) -> None:
    client = ScriptedPlanClient(*[invalid_unit_response() for _ in range(budget + 1)])
    result = LLMPlanner(
        client=client, registry=builtin_registry(), max_repair_attempts=budget
    ).generate(instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals"))
    assert len(client.calls) == budget + 1
    assert result.validation and not result.validation.valid
    assert all(a.diagnostic_codes == ("PARAMETER_TYPE_MISMATCH",) for a in result.attempts)


@pytest.mark.parametrize("budget", [-1, 3, True, 1.5])
def test_invalid_budget_is_rejected_before_requests(budget: Any) -> None:
    client = ScriptedPlanClient()
    with pytest.raises(ValueError, match="integer between 0 and 2"):
        LLMPlanner(client=client, registry=builtin_registry(), max_repair_attempts=budget)
    assert not client.calls


def test_exhausted_malformed_output_errors_are_safe_and_inspectable(caplog) -> None:
    private = "sensitive-source-marker"
    client = ScriptedPlanClient(private, {"unexpected": private}, private)
    with pytest.raises(PlanGenerationError, match="Invalid JSON response") as failure:
        LLMPlanner(client=client, registry=builtin_registry()).generate(
            instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals")
        )
    assert len(client.calls) == 3
    assert len(failure.value.attempts) == 3
    assert private not in str(failure.value) + repr(failure.value.attempts) + caplog.text
    assert failure.value.__cause__ is None


@pytest.mark.parametrize("after_invalid", [False, True])
def test_completion_failures_stop_without_retry_or_secret_leak(after_invalid: bool, caplog) -> None:
    private = "credential-and-raw-provider-marker"
    responses = [invalid_unit_response()] if after_invalid else []
    client = ScriptedPlanClient(*responses, RuntimeError(private))
    planner = LLMPlanner(client=client, registry=builtin_registry())
    kwargs = {"instruction": CAPITALS_INSTRUCTION, "dataset_profile": workbook_profile("capitals")}
    if after_invalid:
        result = planner.generate(**kwargs)
        assert result.validation and not result.validation.valid
        attempts = result.attempts
    else:
        with pytest.raises(PlanGenerationError, match="Model completion failed") as failure:
            planner.generate(**kwargs)
        attempts = failure.value.attempts
        assert private not in str(failure.value)
    assert len(client.calls) == (2 if after_invalid else 1)
    assert attempts[-1].diagnostic_codes == ("COMPLETION_ERROR",)
    assert private not in json.dumps([asdict(a) for a in attempts]) + caplog.text


@pytest.mark.parametrize(
    "instruction,reason,status",
    [
        ("Find driving routes.", "Driving routes are not supported.", "unsupported"),
        ("Calculate distances.", "Specify the distance type.", "ambiguous"),
        (
            CAPITALS_INSTRUCTION,
            "The request involves calculating geographic distances, which requires input values "
            "not currently available in the dataset.",
            "unsupported",
        ),
    ],
)
def test_first_attempt_abstention_is_final_even_with_composable_contracts(
    instruction: str, reason: str, status: str
) -> None:
    """The observed refusal is context for prompt improvements, never a coerced ready plan."""
    client = ScriptedPlanClient({"status": status, "plan": None, "reason": reason})
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=instruction, dataset_profile=workbook_profile("capitals")
    )
    assert result.response.status == status and result.response.reason == reason
    assert result.response.plan is None and result.validation is None
    assert len(client.calls) == 1
    assert [(a.outcome, a.diagnostic_codes) for a in result.attempts] == [(status, ())]


def misnamed_input_response() -> dict[str, Any]:
    """Observed gpt-4o-mini proposal: the geocode input was named after the column header."""
    response = ready_response()
    for step in response["plan"]["steps"][:2]:
        step["inputs"]["capital"] = step["inputs"].pop("city")
    return response


OBSERVED_REFUSAL = {
    "status": "unsupported",
    "plan": None,
    "reason": "The geocode capability requires an input 'city' instead of 'capital', "
    "which was used in the original plan.",
}


def test_abstention_answering_diagnostics_is_challenged_and_can_recover() -> None:
    """Replay the observed failure: a fixable input name, then a refusal restating the fix."""
    invalid = misnamed_input_response()
    client = ScriptedPlanClient(invalid, OBSERVED_REFUSAL, ready_response())
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals")
    )
    assert result.response.status == "ready"
    assert result.validation and result.validation.valid
    assert [(a.outcome, a.diagnostic_codes) for a in result.attempts] == [
        ("invalid", ("MISSING_REQUIRED_INPUT", "UNKNOWN_INPUT")),
        ("unsupported", ("ABSTENTION_AFTER_FEEDBACK",)),
        ("ready", ()),
    ]
    challenge = client.calls[2][0]
    assert challenge[:2] == client.calls[0][0] and len(challenge) == 4
    # The earlier proposal is replayed with its unchanged diagnostics, not the refusal.
    assert json.loads(challenge[2]["content"]) == invalid
    body = json.loads(challenge[3]["content"])
    first_feedback = json.loads(client.calls[1][0][3]["content"])["validation_feedback"]
    assert body["validation_feedback"] == {
        "code": "ABSTENTION_AFTER_FEEDBACK",
        "abstention_reason": OBSERVED_REFUSAL["reason"],
        "errors": first_feedback["errors"],
    }
    assert "not accepted yet" in body["request"]
    # An ordinary repair request no longer suggests abstaining.
    assert "plan null" not in json.loads(client.calls[1][0][3]["content"])["request"]


@pytest.mark.parametrize(
    "reason,status",
    [("Driving routes are not supported.", "unsupported"), ("Specify the type.", "ambiguous")],
)
def test_abstention_repeated_after_one_challenge_is_final(reason: str, status: str) -> None:
    """The planner never overrides a confirmed abstention or coerces a plan."""
    refusal = {"status": status, "plan": None, "reason": reason}
    client = ScriptedPlanClient(invalid_unit_response(), refusal, refusal)
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals")
    )
    assert result.response.status == status and result.response.reason == reason
    assert result.response.plan is None and result.validation is None
    assert len(client.calls) == 3
    assert [(a.outcome, a.diagnostic_codes) for a in result.attempts] == [
        ("invalid", ("PARAMETER_TYPE_MISMATCH",)),
        (status, ("ABSTENTION_AFTER_FEEDBACK",)),
        (status, ()),
    ]


@pytest.mark.parametrize("budget", [1, 2])
def test_unconfirmed_abstention_at_budget_keeps_last_invalid_plan(budget: int) -> None:
    """Without budget for a challenge, the inspectable invalid DAG is retained."""
    invalid = invalid_unit_response()
    client = ScriptedPlanClient(*[invalid] * budget, OBSERVED_REFUSAL)
    result = LLMPlanner(
        client=client, registry=builtin_registry(), max_repair_attempts=budget
    ).generate(instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals"))
    assert len(client.calls) == budget + 1
    assert result.response.plan == EnrichmentPlan.model_validate(invalid["plan"])
    assert result.validation and not result.validation.valid
    assert result.attempts[-1].outcome == "unsupported"
    assert result.attempts[-1].diagnostic_codes == ("ABSTENTION_AFTER_FEEDBACK",)
