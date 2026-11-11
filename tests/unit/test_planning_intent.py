"""Issue #119: offline contract/intent regressions, not evidence of model quality."""

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
from ai_data_enrichment.validation.intent_policy import constraints_from_instruction
from ai_data_enrichment.validation.plan_validator import PlanValidator


def role_response(value: Any = "country_capital", nodes: tuple[int, ...] = (0, 1)) -> dict:
    response = ready_response()
    for index in nodes:
        response["plan"]["steps"][index]["parameters"] = {"required_role": value}
    response["plan"]["assumptions"] = ["No ambiguous city names are present."]
    return response


def test_optional_parameter_metadata_and_prompt_permit_omission() -> None:
    registry = builtin_registry()
    geocode = registry.get("geocode")
    assert geocode
    assert geocode.parameters["required_role"].allowed_values == ["country_capital"]
    assert "Omit this parameter" in geocode.parameters["required_role"].description
    client = ScriptedPlanClient(ready_response())
    result = LLMPlanner(client=client, registry=registry).generate(
        instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals")
    )
    assert result.validation and result.validation.valid and result.validation.contract_valid
    assert result.response.plan
    assert all(s.parameters == {} for s in result.response.plan.steps[:2])
    prompt = client.calls[0][0][0]["content"]
    for text in (
        "may be omitted entirely",
        "allowed_values governs values only if provided",
        "Never use null or unsupported values",
        "parameters={} is valid",
        "Model assumptions are unverified claims",
    ):
        assert text in prompt


@pytest.mark.parametrize(
    "value,code",
    [
        (None, "PARAMETER_TYPE_MISMATCH"),
        ("none", "INVALID_PARAMETER_VALUE"),
        ("", "INVALID_PARAMETER_VALUE"),
    ],
)
def test_disabled_role_values_remain_invalid(value: Any, code: str) -> None:
    plan = EnrichmentPlan.model_validate(role_response(value)["plan"])
    validation = PlanValidator(builtin_registry(), workbook_profile("capitals")).validate(
        plan, instruction=CAPITALS_INSTRUCTION
    )
    assert not validation.valid and not validation.contract_valid
    assert [e.code for e in validation.errors] == [code, code]


@pytest.mark.parametrize("nodes", [(0,), (1,), (0, 1)])
def test_observed_capital_role_passes_contract_but_conflicts_with_user_intent(nodes) -> None:
    plan = EnrichmentPlan.model_validate(role_response(nodes=nodes)["plan"])
    before = plan.model_dump()
    validator = PlanValidator(builtin_registry(), workbook_profile("capitals"))
    assert validator.validate(plan).valid  # Existing independent contract remains strict.
    validation = validator.validate(plan, instruction=CAPITALS_INSTRUCTION)
    assert validation.contract_valid and not validation.valid
    assert {e.step_id for e in validation.errors} == {plan.steps[i].id for i in nodes}
    assert all(e.category == "intent" for e in validation.errors)
    assert all(e.field == "parameters.required_role" for e in validation.errors)
    assert plan.model_dump() == before  # Never silently remove a constraint.


@pytest.mark.parametrize(
    "instruction",
    [
        "Treat Capital_From and Capital_To as literal city names, "
        "not necessarily country capitals. "
        'Do not set required_role="country_capital" on either geocode step. '
        "Use the country columns only to disambiguate geographic locations. "
        "Do not assume that geocoding results will be unambiguous.",
        "Do not require country-capital status.",
        "Do not require capital status.",
        "Use cities regardless of administrative role.",
        "Calculate literal-city distances in km.",
    ],
)
def test_explicit_prohibitions_accept_omission_and_reject_roles(instruction: str) -> None:
    validator = PlanValidator(builtin_registry(), workbook_profile("capitals"))
    assert validator.validate(
        EnrichmentPlan.model_validate(ready_response()["plan"]), instruction=instruction
    ).valid
    assert not validator.validate(
        EnrichmentPlan.model_validate(role_response()["plan"]), instruction=instruction
    ).valid


@pytest.mark.parametrize(
    "instruction",
    [
        "Geocode the country capitals and calculate their distance in km.",
        "Only accept country capitals; calculate their distance in km.",
        "Require country-capital status for both locations.",
        "Use literal city names. Only accept country capitals.",
        'Set required_role="country_capital" on both geocode steps.',
        'Use literal city names and set required_role="country_capital" on both steps.',
    ],
)
def test_explicit_capital_queries_remain_supported(instruction: str) -> None:
    client = ScriptedPlanClient(role_response())
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=instruction, dataset_profile=workbook_profile("capitals")
    )
    assert result.validation and result.validation.valid
    assert len(client.calls) == 1


def test_explicit_required_constraint_detects_missing_role() -> None:
    validator = PlanValidator(builtin_registry(), workbook_profile("capitals"))
    validation = validator.validate(
        EnrichmentPlan.model_validate(ready_response()["plan"]),
        instruction="Only accept country capitals.",
    )
    assert validation.contract_valid and not validation.valid
    assert len(validation.errors) == 2


def test_headers_samples_and_model_claims_are_not_policy_evidence() -> None:
    assert (
        constraints_from_instruction("Calculate distances between Capital_From and Capital_To.")
        == ()
    )
    assert constraints_from_instruction("Geocode Istanbul, Turkey.") == ()
    assert constraints_from_instruction("Find mountain elevations.") == ()


def test_role_conflict_repairs_via_diagnostics_and_independent_revalidation() -> None:
    invalid = role_response()
    client = ScriptedPlanClient(invalid, ready_response())
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals")
    )
    assert result.validation and result.validation.valid
    assert [a.outcome for a in result.attempts] == ["invalid", "ready"]
    assert result.attempts[0].diagnostic_codes == ("INTENT_PARAMETER_CONFLICT",)
    feedback = json.loads(client.calls[1][0][-1]["content"])["validation_feedback"]
    assert feedback["code"] == "INVALID_PLAN"
    assert len(feedback["errors"]) == 2
    assert all(e["category"] == "intent" for e in feedback["errors"])
    assert json.loads(client.calls[1][0][2]["content"]) == invalid


@pytest.mark.parametrize("budget", [0, 1, 2])
def test_role_conflicts_cannot_escape_bounded_repair(budget: int) -> None:
    client = ScriptedPlanClient(*[role_response() for _ in range(budget + 1)])
    result = LLMPlanner(
        client=client, registry=builtin_registry(), max_repair_attempts=budget
    ).generate(instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals"))
    assert result.validation and result.validation.contract_valid and not result.validation.valid
    assert len(client.calls) == budget + 1
    assert all(a.outcome == "invalid" for a in result.attempts)


@pytest.mark.parametrize(
    "reason,status",
    [
        (
            "Parameter 'required_role' in the geocode steps cannot be set to None, "
            "it must be a string value.",
            "unsupported",
        ),
        ("Driving routes are not supported.", "unsupported"),
        ("Please clarify which city you mean.", "ambiguous"),
    ],
)
@pytest.mark.parametrize("after_conflict", [False, True])
def test_abstentions_remain_truthful_and_final(reason, status, after_conflict) -> None:
    """Final at once on the first attempt; after a conflict only when repeated once."""
    refusal = {"status": status, "plan": None, "reason": reason}
    responses = [role_response(), refusal] if after_conflict else []
    client = ScriptedPlanClient(*responses, refusal)
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals")
    )
    assert result.response.status == status and result.response.reason == reason
    assert result.response.plan is None and result.validation is None
    assert len(client.calls) == 1 + 2 * after_conflict
    if after_conflict:
        assert [(a.outcome, a.diagnostic_codes) for a in result.attempts] == [
            ("invalid", ("INTENT_PARAMETER_CONFLICT",)),
            (status, ("ABSTENTION_AFTER_FEEDBACK",)),
            (status, ()),
        ]


def test_null_repairs_by_omission_and_every_repair_checks_intent():
    client = ScriptedPlanClient(role_response(None), role_response(), ready_response())
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals")
    )
    assert result.validation and result.validation.valid
    assert [a.diagnostic_codes for a in result.attempts] == [
        ("PARAMETER_TYPE_MISMATCH",),
        ("INTENT_PARAMETER_CONFLICT",),
        (),
    ]
    assert result.response.plan
    assert all(s.parameters == {} for s in result.response.plan.steps[:2])


def test_conflicting_user_role_requirements_need_clarification():
    instruction = "Only accept country capitals. Do not require capital status."
    validator = PlanValidator(builtin_registry(), workbook_profile("capitals"))
    for proposal in (ready_response(), role_response()):
        validation = validator.validate(
            EnrichmentPlan.model_validate(proposal["plan"]), instruction=instruction
        )
        assert validation.contract_valid and not validation.valid
    constraints = constraints_from_instruction(instruction)
    assert {c.mode for c in constraints} == {"required", "forbidden"}
    assert all(c.evidence for c in constraints)
