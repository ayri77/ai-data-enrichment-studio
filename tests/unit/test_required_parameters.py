"""Required capability parameters: the validator must not be weaker than the handlers."""

import json
from typing import Any

import pytest
from tests.support.planning import (
    CAPITALS_INSTRUCTION,
    ScriptedPlanClient,
    ready_response,
    workbook_profile,
)

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.models import EntityRef, GeoPoint, RuntimeValue
from ai_data_enrichment.plan_run_worker import builtin_registry
from ai_data_enrichment.planning.llm_planner import LLMPlanner
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.providers.wikidata import (
    WikidataElevationHandler,
    WikidataMountainResolver,
)
from ai_data_enrichment.validation.plan_validator import PlanValidator


class UnreachableTransport:
    """A handler must reject a missing parameter before any provider request."""

    def get_json(self, **_kwargs: Any) -> Any:
        raise AssertionError("The provider must not be called.")


HANDLERS: dict[str, tuple[CapabilityHandler, dict[str, RuntimeValue]]] = {
    "geodesic_distance": (
        GeodesicDistanceHandler(),
        {
            "point_a": GeoPoint(latitude=50.45, longitude=30.52),
            "point_b": GeoPoint(latitude=48.86, longitude=2.35),
        },
    ),
    "resolve_entity": (
        WikidataMountainResolver(UnreachableTransport()),
        {"name": "Hoverla", "country": "Ukraine"},
    ),
    "lookup_property": (
        WikidataElevationHandler(UnreachableTransport()),
        {"entity": EntityRef(source="wikidata", entity_id="Q1")},
    ),
}

REQUIRED = [
    (capability.id, name)
    for capability in get_builtin_capabilities()
    for name, parameter in capability.parameters.items()
    if parameter.required
]


@pytest.mark.parametrize("capability_id,parameter", REQUIRED)
def test_handlers_reject_every_parameter_the_contract_declares_required(
    capability_id: str, parameter: str
) -> None:
    """If this fails, the contract or the handler changed and they no longer agree."""
    handler, inputs = HANDLERS[capability_id]
    result = handler.execute(inputs=inputs, parameters={})
    assert result.status == "failure" and result.error_code == "INVALID_PARAMETER", parameter


@pytest.mark.parametrize("scenario", ["capitals", "mountains"])
def test_omitting_a_required_parameter_is_reported_before_approval(scenario: str) -> None:
    payload = ready_response(scenario)["plan"]
    validator = PlanValidator(builtin_registry(), workbook_profile(scenario))
    assert validator.validate(EnrichmentPlan.model_validate(payload)).valid

    required = dict(REQUIRED)
    checked = 0
    for index, step in enumerate(payload["steps"]):
        parameter = required.get(step["capability"])
        if parameter is None:
            continue
        broken = json.loads(json.dumps(payload))
        del broken["steps"][index]["parameters"][parameter]
        result = validator.validate(EnrichmentPlan.model_validate(broken))
        assert not result.valid and not result.contract_valid
        assert [(e.code, e.step_id, e.field) for e in result.errors] == [
            ("MISSING_REQUIRED_PARAMETER", step["id"], f"parameters.{parameter}")
        ]
        assert "Allowed values" in result.errors[0].message
        checked += 1
    assert checked == (1 if scenario == "capitals" else 2)


def test_optional_parameter_may_still_be_omitted() -> None:
    plan = EnrichmentPlan.model_validate(ready_response("capitals")["plan"])
    assert all(step.parameters == {} for step in plan.steps[:2])  # geocode.required_role
    assert PlanValidator(builtin_registry(), workbook_profile("capitals")).validate(plan).valid


def test_missing_required_parameter_is_repaired_from_the_diagnostic() -> None:
    invalid = ready_response()
    invalid["plan"]["steps"][-1]["parameters"] = {}
    client = ScriptedPlanClient(invalid, ready_response())
    result = LLMPlanner(client=client, registry=builtin_registry()).generate(
        instruction=CAPITALS_INSTRUCTION, dataset_profile=workbook_profile("capitals")
    )
    assert result.validation and result.validation.valid
    assert [(a.outcome, a.diagnostic_codes) for a in result.attempts] == [
        ("invalid", ("MISSING_REQUIRED_PARAMETER",)),
        ("ready", ()),
    ]
    feedback = json.loads(client.calls[1][0][-1]["content"])["validation_feedback"]
    assert feedback["errors"][0]["field"] == "parameters.unit"
    assert "['km']" in feedback["errors"][0]["message"]
