"""Offline tests for LLM-based plan repair."""

import json
from typing import Any

import pytest

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.planning.instructions import PLAN_CONTRACT_GUIDANCE
from ai_data_enrichment.planning.llm_plan_repairer import LLMPlanRepairer
from ai_data_enrichment.validation.models import (
    ValidationDiagnostic,
    ValidationResult,
)


class FakeCompletionClient:
    """Capture the repair prompt and return predefined model output."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> str:
        self.calls.append((messages, response_schema))
        return self.response


def make_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)
    return registry


def make_profile() -> DatasetProfile:
    return DatasetProfile(
        dataset_id="mountains",
        name="Mountain elevations",
        columns=[
            ColumnProfile(
                name="Mountain",
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
            ),
        ],
    )


def make_invalid_plan() -> EnrichmentPlan:
    """Structurally valid plan containing an unknown capability."""
    return EnrichmentPlan.model_validate(
        {
            "version": "1.0",
            "steps": [
                {
                    "id": "resolve",
                    "capability": "unknown_capability",
                    "inputs": {},
                    "parameters": {},
                }
            ],
            "outputs": [],
            "assumptions": [],
        }
    )


def make_validation() -> ValidationResult:
    return ValidationResult(
        errors=[
            ValidationDiagnostic(
                code="UNSUPPORTED_CAPABILITY",
                message="Unknown capability: unknown_capability",
                step_id="resolve",
            )
        ]
    )


def test_repair_sends_diagnostics_and_parses_response() -> None:
    """Repair input includes original plan, capability contracts and errors."""

    candidate = make_invalid_plan().model_copy(
        update={
            "steps": [
                make_invalid_plan().steps[0].model_copy(update={"capability": "resolve_entity"})
            ]
        }
    )

    client = FakeCompletionClient(candidate.model_dump_json())
    repairer = LLMPlanRepairer(
        client=client,
        registry=make_registry(),
    )

    result = repairer.repair(
        plan=make_invalid_plan(),
        validation=make_validation(),
        dataset_profile=make_profile(),
        instruction="Resolve mountain names.",
    )

    assert result == candidate
    assert len(client.calls) == 1

    messages, schema = client.calls[0]
    assert PLAN_CONTRACT_GUIDANCE in messages[0]["content"]
    context = json.loads(messages[1]["content"])

    assert context["instruction"] == "Resolve mountain names."
    assert context["dataset_profile"]["dataset_id"] == "mountains"
    assert context["invalid_plan"]["steps"][0]["capability"] == "unknown_capability"
    assert context["validation_errors"][0]["code"] == "UNSUPPORTED_CAPABILITY"
    assert context["validation_errors"][0]["step_id"] == "resolve"
    assert "resolve_entity" in json.dumps(context["capabilities"])
    assert "steps" in schema["properties"]


def test_repair_rejects_invalid_json() -> None:
    """Malformed JSON cannot be used as a repaired plan."""

    client = FakeCompletionClient('{"steps":')
    repairer = LLMPlanRepairer(
        client=client,
        registry=make_registry(),
    )

    with pytest.raises(ValueError, match="not valid JSON"):
        repairer.repair(
            plan=make_invalid_plan(),
            validation=make_validation(),
            dataset_profile=make_profile(),
            instruction=None,
        )


def test_repair_rejects_invalid_schema() -> None:
    """Valid JSON must still satisfy the EnrichmentPlan schema."""

    client = FakeCompletionClient(
        json.dumps(
            {
                "version": "1.0",
                "steps": "not a list",
                "outputs": [],
                "assumptions": [],
            }
        )
    )

    repairer = LLMPlanRepairer(
        client=client,
        registry=make_registry(),
    )

    with pytest.raises(ValueError, match="does not match EnrichmentPlan schema"):
        repairer.repair(
            plan=make_invalid_plan(),
            validation=make_validation(),
            dataset_profile=make_profile(),
            instruction=None,
        )


def test_repair_requires_validation_errors() -> None:
    """A valid plan must not trigger unnecessary LLM repair calls."""

    client = FakeCompletionClient("{}")
    repairer = LLMPlanRepairer(
        client=client,
        registry=make_registry(),
    )

    with pytest.raises(ValueError, match="without validation errors"):
        repairer.repair(
            plan=make_invalid_plan(),
            validation=ValidationResult(),
            dataset_profile=make_profile(),
            instruction=None,
        )

    assert client.calls == []
