"""Requested columns that no capability can fill: the answer, its validation and the repair loop."""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from tests.support.planning import (
    CAPITALS_MAYOR_INSTRUCTION,
    ScriptedPlanClient,
    ready_response,
    workbook_profile,
)

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.plan_run_worker import builtin_registry
from ai_data_enrichment.planning.llm_planner import LLMPlanner
from ai_data_enrichment.planning.models import PlanningResponse
from ai_data_enrichment.validation.plan_validator import (
    PlanValidationPolicy,
    PlanValidator,
    apply_unfilled_targets,
)

TARGETS = PlanValidationPolicy(output_targets=frozenset({"distance", "mayor"}))


def partial(unfilled: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    """The prepared capitals plan (it writes `distance`), answered with some targets unfilled."""
    return {
        **ready_response(),
        "unfilled_targets": ["mayor"] if unfilled is None else unfilled,
        **extra,
    }


def writing_both() -> dict[str, Any]:
    response = ready_response()
    first = response["plan"]["outputs"][0]
    response["plan"]["outputs"].append({**first, "target_column": "mayor"})
    return {**response, "unfilled_targets": ["mayor"]}


def empty_plan(unfilled: list[str]) -> dict[str, Any]:
    plan = {"version": "1.0", "steps": [], "outputs": [], "assumptions": []}
    return {"status": "ready", "plan": plan, "reason": None, "unfilled_targets": unfilled}


def generate(client: ScriptedPlanClient, policy: PlanValidationPolicy | None = TARGETS):
    return LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=CAPITALS_MAYOR_INSTRUCTION,
        dataset_profile=workbook_profile("capitals"),
        validation_policy=policy,
    )


def feedback_codes(client: ScriptedPlanClient, call: int) -> list[str]:
    feedback = json.loads(client.calls[call][0][-1]["content"])["validation_feedback"]
    return [error["code"] for error in feedback["errors"]]


# --- the answer ------------------------------------------------------------------------------


def test_the_answer_has_an_empty_list_by_default_and_keeps_forbidding_other_fields() -> None:
    response = PlanningResponse.model_validate(ready_response())
    assert response.unfilled_targets == []
    with pytest.raises(ValidationError):
        PlanningResponse.model_validate({**ready_response(), "unexpected": 1})


def test_a_ready_answer_may_name_unfilled_targets() -> None:
    response = PlanningResponse.model_validate(partial(["mayor", "age"]))
    assert response.unfilled_targets == ["mayor", "age"]


@pytest.mark.parametrize("status", ["unsupported", "ambiguous"])
def test_only_a_ready_answer_may_name_unfilled_targets(status: str) -> None:
    with pytest.raises(ValidationError, match="Only a ready response"):
        PlanningResponse.model_validate(
            {"status": status, "plan": None, "reason": "no", "unfilled_targets": ["mayor"]}
        )


@pytest.mark.parametrize("names", [[""], ["  "], ["mayor", " "]])
def test_a_blank_name_is_refused(names: list[str]) -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        PlanningResponse.model_validate(partial(names))


def test_a_name_listed_twice_is_refused() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        PlanningResponse.model_validate(partial(["mayor", "mayor"]))


def test_a_ready_answer_that_fills_nothing_is_not_ready() -> None:
    with pytest.raises(ValidationError, match="at least one step and one output"):
        PlanningResponse.model_validate(empty_plan(["distance", "mayor"]))
    for cut in ("steps", "outputs"):
        response = partial()
        response["plan"][cut] = []
        with pytest.raises(ValidationError, match="at least one step and one output"):
            PlanningResponse.model_validate(response)


def test_an_empty_plan_without_unfilled_targets_is_left_to_the_validator_as_before() -> None:
    assert PlanningResponse.model_validate(empty_plan([])).plan is not None


# --- the one place that reduces the targets ---------------------------------------------------


def plan_of(response: dict[str, Any]) -> EnrichmentPlan:
    return EnrichmentPlan.model_validate(response["plan"])


def test_the_effective_targets_are_the_requested_ones_minus_the_unfilled() -> None:
    policy, errors = apply_unfilled_targets(TARGETS, ["mayor"], plan_of(partial()))
    assert errors == []
    assert policy is not None and policy.output_targets == frozenset({"distance"})
    assert TARGETS.output_targets == frozenset({"distance", "mayor"})  # the caller's is untouched
    # Nothing named: the very same policy, whatever it is.
    assert apply_unfilled_targets(TARGETS, [], plan_of(partial())) == (TARGETS, [])
    assert apply_unfilled_targets(None, [], None) == (None, [])


@pytest.mark.parametrize(
    "policy,unfilled,response,code",
    [
        (TARGETS, ["bogus"], partial(["bogus"]), "UNKNOWN_UNFILLED_TARGET"),
        (TARGETS, ["mayor"], writing_both(), "UNFILLED_TARGET_ALSO_WRITTEN"),
        (None, ["mayor"], partial(), "UNFILLED_TARGETS_WITHOUT_TARGETS"),
        (
            PlanValidationPolicy(max_steps=9),
            ["mayor"],
            partial(),
            "UNFILLED_TARGETS_WITHOUT_TARGETS",
        ),
    ],
)
def test_a_wrongly_named_target_is_reported_and_reduces_nothing(
    policy: PlanValidationPolicy | None, unfilled: list[str], response: dict[str, Any], code: str
) -> None:
    reduced, errors = apply_unfilled_targets(policy, unfilled, plan_of(response))
    assert [e.code for e in errors] == [code]
    assert reduced == policy


# --- the planner loop -------------------------------------------------------------------------


def test_a_partial_answer_is_accepted_and_validated_against_the_reduced_set() -> None:
    client = ScriptedPlanClient(partial())
    result = generate(client)
    assert result.validation is not None and result.validation.valid
    assert result.unfilled_targets == ("mayor",)
    assert result.response.status == "ready" and len(client.calls) == 1
    assert [(a.number, a.outcome) for a in result.attempts] == [(1, "ready")]


def test_the_plan_must_still_write_every_target_that_is_not_listed() -> None:
    # `mayor` is requested and neither written nor listed: the existing rule, unchanged.
    client = ScriptedPlanClient(partial([]), partial(["mayor"]))
    result = generate(client)
    assert feedback_codes(client, 1) == ["OUTPUT_TARGET_MISMATCH"]
    assert result.validation is not None and result.validation.valid
    assert result.unfilled_targets == ("mayor",)


def test_a_full_plan_with_an_empty_list_behaves_as_before() -> None:
    client = ScriptedPlanClient(ready_response())
    result = generate(client, PlanValidationPolicy(output_targets=frozenset({"distance"})))
    assert result.validation is not None and result.validation.valid
    assert result.unfilled_targets == ()
    assert result.response.unfilled_targets == [] and len(client.calls) == 1


@pytest.mark.parametrize(
    "wrong,code,policy",
    [
        (partial(["bogus"]), "UNKNOWN_UNFILLED_TARGET", TARGETS),
        (writing_both(), "UNFILLED_TARGET_ALSO_WRITTEN", TARGETS),
        (partial(["mayor"]), "UNFILLED_TARGETS_WITHOUT_TARGETS", None),
    ],
)
def test_a_wrongly_named_target_goes_back_through_the_repair_loop(
    wrong: dict[str, Any], code: str, policy: PlanValidationPolicy | None
) -> None:
    right = partial(["mayor"]) if policy is TARGETS else ready_response()
    client = ScriptedPlanClient(wrong, right)
    result = generate(client, policy)
    assert len(client.calls) == 2
    assert code in feedback_codes(client, 1)
    assert result.attempts[0].outcome == "invalid"
    assert code in result.attempts[0].diagnostic_codes
    assert result.validation is not None and result.validation.valid
    assert result.unfilled_targets == (("mayor",) if policy is TARGETS else ())


def test_an_answer_that_lists_every_target_with_an_empty_plan_is_refused_and_repaired() -> None:
    client = ScriptedPlanClient(empty_plan(["distance", "mayor"]), partial())
    result = generate(client)
    feedback = json.loads(client.calls[1][0][-1]["content"])["validation_feedback"]
    assert feedback["code"] == "INVALID_PLANNING_SCHEMA"
    assert "at least one step and one output" in json.dumps(feedback)
    assert result.validation is not None and result.validation.valid
    assert result.unfilled_targets == ("mayor",)


def test_listing_every_target_beside_a_plan_that_writes_one_is_not_accepted() -> None:
    wrong = partial(["distance", "mayor"])  # the plan writes `distance`
    client = ScriptedPlanClient(wrong, partial())
    result = generate(client)
    assert "UNFILLED_TARGET_ALSO_WRITTEN" in feedback_codes(client, 1)
    assert result.unfilled_targets == ("mayor",)


def test_the_schema_the_model_is_given_follows_the_answer_model() -> None:
    client = ScriptedPlanClient(partial())
    generate(client)
    schema = client.calls[0][1]
    assert schema == PlanningResponse.model_json_schema()
    assert schema["properties"]["unfilled_targets"]["items"] == {"type": "string"}
    assert "unfilled_targets" not in schema["required"]


def test_what_the_planner_is_told_about_partial_plans_and_kept_cells_never_mentions_a_search() -> (
    None
):
    client = ScriptedPlanClient(partial())
    generate(client)
    system = client.calls[0][0][0]["content"]
    assert "list the names of the others in unfilled_targets" in system
    assert "already hold a value are kept by the run" in system
    texts = json.dumps(client.calls[0][0]) + json.dumps(client.calls[0][1])
    for forbidden in ("web_lookup", "web search", "AI search", "fallback", "universal search"):
        assert forbidden.lower() not in texts.lower()


def test_a_validated_partial_plan_is_valid_for_the_validator_only_with_the_reduced_set() -> None:
    plan = plan_of(partial())
    registry, profile = builtin_registry(), workbook_profile("capitals")
    assert not PlanValidator(registry, profile, TARGETS).validate(plan).valid
    reduced, _ = apply_unfilled_targets(TARGETS, ["mayor"], plan)
    assert PlanValidator(registry, profile, reduced).validate(plan).valid
