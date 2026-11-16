"""Freeze everything the planning model is shown.

The planner is measured live (see docs/26-live-planning-reliability.md). Any change to
what it is shown, however small, makes that measurement stale. These tests compare the
capability descriptions, the response schema, the fixed instruction texts and one
complete request with the committed files in tests/unit/snapshots/planner_surface/.

To update the snapshots after a deliberate change and a new live measurement, run the
tests once with UPDATE_PLANNER_SURFACE=1. That rewrites the files and fails every test
that rewrote one, so a rewriting run is never taken for a pass. Review the diff, commit
it, then run without the variable.

The files are compared as parsed JSON so that a diff stays readable. Key order inside
an object would escape that comparison, so the complete request also carries a SHA-256
of its exact serialisation: reordering the keys of a capability description changes the
text the model reads, and fails here even when the readable part shows no difference.
A missing snapshot is a failure, never an automatic write.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from ai_data_enrichment.capabilities.definitions import offered_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import ColumnProfile, DatasetProfile, PrimitiveType
from ai_data_enrichment.planning import llm_planner
from ai_data_enrichment.planning.instructions import PLAN_CONTRACT_GUIDANCE
from ai_data_enrichment.planning.llm_planner import LLMPlanner
from ai_data_enrichment.planning.models import PlanningResponse
from ai_data_enrichment.planning.openai_client import OpenAIPlanClient
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy

SNAPSHOTS = Path(__file__).parent / "snapshots" / "planner_surface"


def _assert_frozen(name: str, actual: Any) -> None:
    path = SNAPSHOTS / name
    text = json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_PLANNER_SURFACE") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        pytest.fail(f"UPDATE_PLANNER_SURFACE=1: rewrote {path}. This run is not a pass.")
    assert path.is_file(), f"Snapshot {path} is missing. See the module docstring to create it."
    assert json.loads(text) == json.loads(path.read_text(encoding="utf-8")), (
        f"{name}: this changes what the planning model is shown. Planning reliability "
        "must be measured again (docs/26-live-planning-reliability.md). Only after that, "
        "update the snapshots: run this module once with UPDATE_PLANNER_SURFACE=1, "
        "review and commit the diff."
    )


def _registry(*, road_distance: bool) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for capability in offered_capabilities(road_distance=road_distance):
        registry.register(capability)
    return registry


def _planner_request() -> dict[str, Any]:
    """Run the real planner and the real OpenAI client against a fake API object."""
    api = Mock()
    api.responses.create.return_value.status = "completed"
    api.responses.create.return_value.output_text = json.dumps(
        {"status": "unsupported", "plan": None, "reason": "snapshot"}
    )
    profile = DatasetProfile(
        dataset_id="snapshot",
        name="Snapshot",
        row_count=1,
        columns=[
            ColumnProfile(
                name="City", inferred_type=PrimitiveType.TEXT, null_count=0, sample_values=["Paris"]
            ),
            ColumnProfile(
                name="Country",
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
                sample_values=["France"],
            ),
        ],
        sample_records=[{"City": "Paris", "Country": "France"}],
    )
    LLMPlanner(
        client=OpenAIPlanClient(api=api, model="snapshot-model"),
        registry=_registry(road_distance=True),
    ).generate(
        instruction="Find the latitude of City, using Country. Write it into latitude.",
        dataset_profile=profile,
        validation_policy=PlanValidationPolicy(output_targets=frozenset({"latitude"})),
    )
    assert api.responses.create.call_count == 1
    return dict(api.responses.create.call_args.kwargs)


@pytest.mark.parametrize("road_distance", [False, True])
def test_capability_descriptions_are_frozen(road_distance: bool) -> None:
    name = f"capabilities_road_distance_{str(road_distance).lower()}.json"
    _assert_frozen(name, _registry(road_distance=road_distance).describe_all())


def test_offered_capability_ids() -> None:
    base = ["geocode", "resolve_entity", "lookup_property", "geodesic_distance", "ratio"]
    assert [c.id for c in offered_capabilities(road_distance=False)] == base
    assert [c.id for c in offered_capabilities(road_distance=True)] == [*base, "road_distance"]


def test_planning_response_schema_is_frozen() -> None:
    _assert_frozen("planning_response_schema.json", PlanningResponse.model_json_schema())


def test_instruction_texts_are_frozen() -> None:
    system = _planner_request()["input"][0]["content"]
    assert system.endswith(PLAN_CONTRACT_GUIDANCE)
    _assert_frozen(
        "instruction_texts.json",
        {
            "PLAN_CONTRACT_GUIDANCE": PLAN_CONTRACT_GUIDANCE,
            "_ABSTENTION_CHALLENGE_REQUEST": llm_planner._ABSTENTION_CHALLENGE_REQUEST,
            "_REPAIR_REQUEST": llm_planner._REPAIR_REQUEST,
            "system message built inline in LLMPlanner.generate": system,
        },
    )


def test_complete_request_is_frozen() -> None:
    """The request the OpenAI API receives, the schema prompt included."""
    request = _planner_request()
    exact = json.dumps(request, ensure_ascii=False).encode("utf-8")
    request["sha256 of the exact request, key order included"] = hashlib.sha256(exact).hexdigest()
    system, context, schema = request["input"]
    prefix, _, schema_json = schema["content"].partition("\n")
    request["input"] = [
        system,
        {"role": context["role"], "content (JSON)": json.loads(context["content"])},
        {"role": schema["role"], "prefix": prefix, "schema (JSON)": json.loads(schema_json)},
    ]
    _assert_frozen("complete_request.json", request)
