"""Tests for autonomous agent decision contracts."""

import pytest
from pydantic import ValidationError

from ai_data_enrichment.orchestration.autonomous.contracts import (
    Abstain,
    AgentDecisionPayload,
    Finish,
    ObservationRef,
    SourceFieldRef,
    ToolCall,
)


def test_tool_call_with_source_fields() -> None:
    call = ToolCall(
        call_id="geocode_london",
        capability_id="geocode",
        inputs={
            "city": SourceFieldRef(column="Capital_From"),
            "country": SourceFieldRef(column="Country_From"),
        },
    )

    assert call.capability_id == "geocode"
    assert call.inputs["city"].kind == "source"


def test_tool_call_with_previous_observations() -> None:
    call = ToolCall(
        call_id="distance",
        capability_id="geodesic_distance",
        inputs={
            "point_a": ObservationRef(call_id="geocode_london"),
            "point_b": ObservationRef(call_id="geocode_rome"),
        },
        parameters={"unit": "km"},
    )

    assert call.inputs["point_a"].kind == "observation"
    assert call.parameters["unit"] == "km"


def test_arbitrary_coordinates_cannot_be_tool_inputs() -> None:
    with pytest.raises(ValidationError):
        ToolCall.model_validate(
            {
                "call_id": "distance",
                "capability_id": "geodesic_distance",
                "inputs": {
                    "point_a": {
                        "latitude": 51.5,
                        "longitude": -0.1,
                    },
                },
            }
        )


def test_agent_decision_discriminator() -> None:
    finish = AgentDecisionPayload.model_validate(
        {"kind": "finish", "result_call_id": "distance"}
    ).root

    abstain = AgentDecisionPayload.model_validate(
        {
            "kind": "abstain",
            "reason_code": "ambiguous_entity",
            "message": "Multiple locations require user review.",
        }
    ).root

    assert isinstance(finish, Finish)
    assert isinstance(abstain, Abstain)


def test_unknown_decision_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentDecisionPayload.model_validate({"kind": "execute_python"})
