"""Live LLM demo of autonomous tool selection.

The LLM is real; geocoding uses fixed offline coordinates.
No Excel files are modified.
"""

import os

from openai import OpenAI

# Reuse the existing offline fixture from the M4 demo.
from scripts.demo_m4_agent import OfflineGeocoder

from ai_data_enrichment.capabilities.definitions import (
    get_builtin_capabilities,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.execution.job_models import InputRecord
from ai_data_enrichment.execution.models import Quantity
from ai_data_enrichment.orchestration.autonomous.llm_decision_maker import (
    LLMDecisionMaker,
)
from ai_data_enrichment.orchestration.autonomous.runtime import (
    AutonomousRunner,
)
from ai_data_enrichment.planning.openai_client import OpenAIPlanClient
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler


def main() -> None:
    model = os.getenv("OPENAI_MODEL")

    if not os.getenv("OPENAI_API_KEY") or not model:
        raise SystemExit("Configure OPENAI_API_KEY and OPENAI_MODEL.")

    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)

    allowed = frozenset(
        {
            "geocode",
            "geodesic_distance",
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
            "geocode": OfflineGeocoder(),
            "geodesic_distance": GeodesicDistanceHandler(),
        },
        allowed_capabilities=allowed,
        decision_maker=decision_maker,
        max_decisions=8,
    )

    result = runner.run(
        instruction=(
            "Calculate the straight-line distance in kilometers "
            "between Berlin, Germany and Kyiv, Ukraine. "
            "Use the available tools. Finish with the calculated "
            "distance Quantity, not coordinates. "
            "Do not require a country-capital role."
        ),
        record=InputRecord(
            record_id="Capitals!demo",
            values={
                "Capital_From": "Berlin",
                "Country_From": "Germany",
                "Capital_To": "Kyiv",
                "Country_To": "Ukraine",
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

    print("\n=== Final Result ===")
    print(f"Status: {result.status}")
    print(f"Result call: {result.result_call_id}")
    print(f"Value: {result.value}")
    print(f"Reason: {result.reason}")

    if result.status != "completed" or not isinstance(result.value, Quantity):
        raise SystemExit("Agent did not return a distance.")


if __name__ == "__main__":
    main()
