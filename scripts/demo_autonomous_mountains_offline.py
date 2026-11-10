"""Live LLM demo of autonomous tool selection.

The LLM is real; geocoding uses fixed offline coordinates.
No Excel files are modified.
"""

import os
from collections.abc import Mapping

from openai import OpenAI

from ai_data_enrichment.capabilities.definitions import (
    get_builtin_capabilities,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.execution.job_models import InputRecord
from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.orchestration.autonomous.llm_decision_maker import (
    LLMDecisionMaker,
)
from ai_data_enrichment.orchestration.autonomous.runtime import (
    AutonomousRunner,
)
from ai_data_enrichment.planning.openai_client import OpenAIPlanClient


class OfflineMountainResolver:
    """Resolve Mount Everest from a deterministic fixture."""

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        if (
            inputs.get("name") != "Mount Everest"
            or inputs.get("country") != "Nepal"
            or parameters.get("entity_type") != "mountain"
        ):
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="Mountain not found in fixture.",
            )

        return OperationResult(
            status="success",
            value=EntityRef(
                source="fixture",
                entity_id="everest",
            ),
            evidence=(
                Evidence(
                    source="test_fixture",
                    reference="everest-identity",
                ),
            ),
        )


class OfflineElevationLookup:
    """Return a fixed elevation for the fixture entity."""

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        if (
            inputs.get("entity") != EntityRef(source="fixture", entity_id="everest")
            or parameters.get("property") != "elevation"
        ):
            return OperationResult(
                status="failure",
                error_code="INVALID_INPUT",
                message="Unknown fixture entity or property.",
            )

        return OperationResult(
            status="success",
            value=Quantity(value=8848.86, unit="m"),
            evidence=(
                Evidence(
                    source="test_fixture",
                    reference="everest-elevation",
                ),
            ),
        )


def main() -> None:
    model = os.getenv("OPENAI_MODEL")

    if not os.getenv("OPENAI_API_KEY") or not model:
        raise SystemExit("Configure OPENAI_API_KEY and OPENAI_MODEL.")

    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)

    allowed = frozenset(
        {
            "resolve_entity",
            "lookup_property",
        }
    )

    client = OpenAIPlanClient(
        api=OpenAI(timeout=45.0, max_retries=0),
        model=model,
        max_output_tokens=4096,
    )

    decision_maker = LLMDecisionMaker(
        client=client,
        registry=registry,
        allowed_capabilities=allowed,
    )

    runner = AutonomousRunner(
        registry=registry,
        handlers={
            "resolve_entity": OfflineMountainResolver(),
            "lookup_property": OfflineElevationLookup(),
        },
        allowed_capabilities=allowed,
        decision_maker=decision_maker,
        max_decisions=8,
    )

    result = runner.run(
        instruction=(
            "Find the elevation of Mount Everest in Nepal, "
            "in meters. Resolve the mountain using the "
            "available tools, look up its elevation, and "
            "finish with the resulting Quantity."
        ),
        record=InputRecord(
            record_id="Mountains!demo",
            values={
                "Mountain": "Mount Everest",
                "Country": "Nepal",
            },
        ),
    )

    print("\n=== Agent Execution Trace ===")

    for index, observation in enumerate(result.observations, start=1):
        print(
            f"{index}. "
            f"{observation.call_id}: "
            f"{observation.capability_id} -> "
            f"{observation.result.status}"
        )
        print(f"   Value: {observation.result.value}")
        print(f"   Evidence: {observation.result.evidence}")
        print(f"   Error: {observation.result.error_code}")
        print(f"   Message: {observation.result.message}")

    print("\n=== Final Result ===")
    print(f"Status: {result.status}")
    print(f"Result call: {result.result_call_id}")
    print(f"Value: {result.value}")
    print(f"Reason: {result.reason}")

    if (
        result.status != "completed"
        or not isinstance(result.value, Quantity)
        or result.value.unit != "m"
    ):
        raise SystemExit("Agent did not return a verified elevation.")


if __name__ == "__main__":
    main()
