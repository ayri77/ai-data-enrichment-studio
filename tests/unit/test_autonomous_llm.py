"""Tests for LLM-based autonomous decisions."""

import json
from typing import Any

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.execution.job_models import InputRecord
from ai_data_enrichment.orchestration.autonomous.contracts import ToolCall
from ai_data_enrichment.orchestration.autonomous.llm_decision_maker import (
    LLMDecisionMaker,
)


class FakeCompletionClient:
    """Return a controlled model response."""

    def __init__(self) -> None:
        self.context: dict[str, Any] = {}

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> str:
        self.context = json.loads(messages[-1]["content"])

        return json.dumps(
            {
                "kind": "tool_call",
                "call_id": "geocode_london",
                "capability_id": "geocode",
                "inputs": {
                    "city": {
                        "kind": "source",
                        "column": "Capital_From",
                    },
                    "country": {
                        "kind": "source",
                        "column": "Country_From",
                    },
                },
            }
        )


def test_llm_decision_uses_current_record_and_tools() -> None:
    registry = CapabilityRegistry()

    for capability in get_builtin_capabilities():
        registry.register(capability)

    client = FakeCompletionClient()

    decision_maker = LLMDecisionMaker(
        client=client,
        registry=registry,
        allowed_capabilities=frozenset({"geocode"}),
    )

    decision = decision_maker.decide(
        instruction="Find coordinates for London.",
        record=InputRecord(
            record_id="Capitals!2",
            values={
                "Capital_From": "London",
                "Country_From": "United Kingdom",
            },
        ),
        observations=(),
    )

    assert isinstance(decision, ToolCall)
    assert decision.capability_id == "geocode"

    assert client.context["record"]["values"]["Capital_From"] == "London"

    assert [tool["id"] for tool in client.context["available_tools"]] == ["geocode"]

    assert client.context["observations"] == []
