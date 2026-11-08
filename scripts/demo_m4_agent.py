"""Offline demonstration of bounded LangGraph orchestration.

Run:
    uv run python -m scripts.demo_m4_agent --mode happy
    uv run python -m scripts.demo_m4_agent --mode repair
"""

import argparse
import json
from collections.abc import Mapping

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.job_executor import JobExecutor
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionPolicy
from ai_data_enrichment.execution.models import GeoPoint, OperationResult, RuntimeValue
from ai_data_enrichment.orchestration.agent.graph import build_agent_graph
from ai_data_enrichment.orchestration.agent.state import AgentState
from ai_data_enrichment.providers.geodesic import GeodesicDistanceHandler
from ai_data_enrichment.validation.models import ValidationResult


class OfflineGeocoder:
    """Fixed demonstration coordinates; no HTTP requests."""

    COORDINATES = {
        ("Berlin", "Germany"): GeoPoint(latitude=52.52, longitude=13.405),
        ("Kyiv", "Ukraine"): GeoPoint(latitude=50.45, longitude=30.5234),
    }

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        city = inputs.get("city")
        country = inputs.get("country")

        point = (
            self.COORDINATES.get((city, country))
            if (isinstance(city, str) and isinstance(country, str))
            else None
        )

        if point is None:
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message="No matching offline coordinate.",
            )

        return OperationResult(status="success", value=point)


def make_plan() -> EnrichmentPlan:
    return EnrichmentPlan.model_validate(
        {
            "version": "1.0",
            "steps": [
                {
                    "id": "origin",
                    "capability": "geocode",
                    "inputs": {
                        "city": {"kind": "column", "name": "City A"},
                        "country": {"kind": "column", "name": "Country A"},
                    },
                    "parameters": {},
                },
                {
                    "id": "destination",
                    "capability": "geocode",
                    "inputs": {
                        "city": {"kind": "column", "name": "City B"},
                        "country": {"kind": "column", "name": "Country B"},
                    },
                    "parameters": {},
                },
                {
                    "id": "distance",
                    "capability": "geodesic_distance",
                    "inputs": {
                        "point_a": {"kind": "step_output", "step_id": "origin"},
                        "point_b": {"kind": "step_output", "step_id": "destination"},
                    },
                    "parameters": {"unit": "km"},
                },
            ],
            "outputs": [
                {
                    "step_id": "distance",
                    "value_field": "value",
                    "target_column": "distance_km",
                    "expected_unit": "km",
                }
            ],
            "assumptions": [],
        }
    )


class OfflineRepairer:
    """Deterministic stand-in for the optional LLM repair adapter."""

    def repair(
        self,
        *,
        plan: EnrichmentPlan,
        validation: ValidationResult,
        dataset_profile: DatasetProfile,
        instruction: str | None,
    ) -> EnrichmentPlan:
        if validation.valid:
            raise ValueError("Repair requires validation errors.")
        return make_plan()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("happy", "repair"), default="happy")
    args = parser.parse_args()

    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)

    profile = DatasetProfile(
        dataset_id="capitals-demo",
        name="Capital distances",
        row_count=1,
        columns=[
            ColumnProfile(
                name=name,
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
            )
            for name in ("City A", "Country A", "City B", "Country B")
        ],
    )

    records = (
        InputRecord(
            record_id="Capitals!2",
            values={
                "City A": "Berlin",
                "Country A": "Germany",
                "City B": "Kyiv",
                "Country B": "Ukraine",
            },
        ),
    )

    executor = JobExecutor(
        handlers={
            "geocode": OfflineGeocoder(),
            "geodesic_distance": GeodesicDistanceHandler(),
        },
        selected_providers={
            "geocode": "offline-fixture",
            "geodesic_distance": "local",
        },
        configuration_ids={
            "geocode": "offline-demo-v1",
            "geodesic_distance": "haversine-v1",
        },
        policy=JobExecutionPolicy(max_rows=10, batch_size=10),
    )

    valid_plan = make_plan()

    if args.mode == "repair":
        invalid_step = valid_plan.steps[0].model_copy(update={"capability": "unknown_capability"})
        initial_plan = valid_plan.model_copy(
            update={"steps": [invalid_step, *valid_plan.steps[1:]]}
        )
    else:
        initial_plan = valid_plan

    graph = build_agent_graph(
        registry=registry,
        executor=executor,
        repairer=OfflineRepairer() if args.mode == "repair" else None,
        max_repair_attempts=2,
    )

    state: AgentState = {
        "instruction": "Calculate the distance between Berlin and Kyiv.",
        "plan": initial_plan,
        "dataset_profile": profile,
        "records": records,
    }

    trace: list[dict[str, object]] = []
    final_status = "unknown"
    repair_attempts = 0
    final_metrics: dict[str, object] | None = None
    distance_km: float | None = None

    for update in graph.stream(state, stream_mode="updates"):
        for node_name, node_state in update.items():
            status = node_state.get("status", "unknown")
            repair_attempts = node_state.get("repair_attempts", repair_attempts)
            final_status = status

            diagnostics = (
                [error.code for error in node_state["validation"].errors]
                if "validation" in node_state
                else []
            )

            trace.append(
                {
                    "node": node_name,
                    "status": status,
                    "repair_attempts": repair_attempts,
                    "diagnostics": diagnostics,
                }
            )

            if "job_result" in node_state:
                result = node_state["job_result"]
                final_metrics = result.metrics.model_dump(mode="json")

                for record in result.records:
                    for step in record.row_result.steps:
                        if step.step_id == "distance" and step.result.status == "success":
                            value = step.result.value
                            if hasattr(value, "value"):
                                distance_km = value.value

    print(
        json.dumps(
            {
                "mode": args.mode,
                "trace": trace,
                "final_status": final_status,
                "repair_attempts": repair_attempts,
                "distance_km": distance_km,
                "metrics": final_metrics,
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    if final_status != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
