"""Test provider-independent LLM plan generation."""

import json
from typing import Any

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.planning.llm_planner import LLMPlanner


class FakePlanClient:
    """Return a predefined JSON response without calling an external model."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> str:
        self.calls.append((messages, response_schema))
        return json.dumps(self.response)


def test_llm_planner_generates_and_validates_mountain_plan() -> None:
    """Compose model context from the request, profile and capability registry."""
    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)

    profile = DatasetProfile(
        dataset_id="mountains",
        name="Mountain dataset",
        columns=[
            ColumnProfile(name="Mountain", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="Country", inferred_type=PrimitiveType.TEXT, null_count=0),
            ColumnProfile(name="height", inferred_type=PrimitiveType.NUMBER, null_count=0),
        ],
    )
    candidate = {
        "version": "1.0",
        "steps": [
            {
                "id": "mountain",
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
                    "entity": {"kind": "step_output", "step_id": "mountain"},
                },
                "parameters": {"property": "elevation"},
            },
        ],
        "outputs": [
            {"step_id": "elevation", "value_field": "value", "target_column": "height"},
        ],
        "assumptions": [],
    }
    client = FakePlanClient(response=candidate)
    planner = LLMPlanner(client=client, registry=registry)

    result = planner.generate(
        instruction="Find the elevation of each mountain in meters.",
        dataset_profile=profile,
    )

    assert result.plan == EnrichmentPlan.model_validate(candidate)
    assert result.validation.valid is True
    assert len(client.calls) == 1

    messages, response_schema = client.calls[0]
    prompt = " ".join(message["content"] for message in messages)
    for expected in ("elevation", "Mountain", "Country", "resolve_entity", "lookup_property"):
        assert expected in prompt
    assert "steps" in response_schema["properties"]
