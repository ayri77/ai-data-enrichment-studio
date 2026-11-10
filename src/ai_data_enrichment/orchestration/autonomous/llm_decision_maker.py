"""LLM-driven decisions for the autonomous agent."""

import json
from typing import Any, Protocol

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.execution.job_models import InputRecord

from .contracts import (
    AgentDecision,
    AgentDecisionPayload,
    ToolObservation,
)


class DecisionCompletionClient(Protocol):
    """Generate JSON using the existing LLM client interface."""

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> str: ...


SYSTEM_PROMPT = """
You are a data enrichment agent.

Choose exactly one next action:
- tool_call: execute one available capability.
- finish: return the ID of a successful tool call containing
  the requested final result.
- abstain: stop when the task cannot be completed reliably.

Rules:
- Use only capabilities listed in available_tools.
- Read source values using source references.
- Read previous tool results using observation references.
- Never invent coordinates, entity IDs, or measurements.
- Do not repeat a successful operation unnecessarily.
- Finish only when the user's requested result is available.
- If a tool fails or a location is ambiguous, do not guess.
- Return one valid JSON decision.

A city is not necessarily a national capital, even when
the source column is named Capital_From or Capital_To.
"""


class LLMDecisionMaker:
    """Choose the next agent action using an LLM."""

    def __init__(
        self,
        *,
        client: DecisionCompletionClient,
        registry: CapabilityRegistry,
        allowed_capabilities: frozenset[str],
    ) -> None:
        self.client = client
        self.registry = registry
        self.allowed_capabilities = allowed_capabilities

    def decide(
        self,
        *,
        instruction: str,
        record: InputRecord,
        observations: tuple[ToolObservation, ...],
    ) -> AgentDecision:
        """Request one decision using the current execution state."""

        available_tools = [
            capability.model_dump(mode="json")
            for capability in self.registry.list_all()
            if capability.id in self.allowed_capabilities
        ]

        context = {
            "instruction": instruction,
            "record": record.model_dump(mode="json"),
            "available_tools": available_tools,
            "observations": [observation.model_dump(mode="json") for observation in observations],
        }

        response = self.client.complete(
            messages=[
                {
                    "role": "developer",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        context,
                        ensure_ascii=False,
                    ),
                },
            ],
            response_schema=AgentDecisionPayload.model_json_schema(),
        )

        return AgentDecisionPayload.model_validate_json(response).root
