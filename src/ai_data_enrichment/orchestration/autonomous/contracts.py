"""Typed decisions and observations for autonomous tool execution."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from ai_data_enrichment.domain.capability import Identifier
from ai_data_enrichment.execution.models import OperationResult


class SourceFieldRef(BaseModel):
    """Read an input value from the current record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["source"] = "source"
    column: str = Field(min_length=1)


class ObservationRef(BaseModel):
    """Read a value from a previously executed tool call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["observation"] = "observation"
    call_id: Identifier


ToolInput = Annotated[
    SourceFieldRef | ObservationRef,
    Field(discriminator="kind"),
]


class ToolCall(BaseModel):
    """Request execution of a registered capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["tool_call"] = "tool_call"
    call_id: Identifier
    capability_id: Identifier
    inputs: dict[Identifier, ToolInput]
    parameters: dict[Identifier, str] = Field(default_factory=dict)


class ToolObservation(BaseModel):
    """Record the actual result returned by a capability handler."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: Identifier
    capability_id: Identifier
    result: OperationResult


class Finish(BaseModel):
    """Finish by referencing an actual successful tool result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["finish"] = "finish"
    result_call_id: Identifier


class Abstain(BaseModel):
    """Stop without producing an unverified result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["abstain"] = "abstain"
    reason_code: Identifier
    message: str = Field(min_length=1)


AgentDecision = Annotated[
    ToolCall | Finish | Abstain,
    Field(discriminator="kind"),
]


class AgentDecisionPayload(RootModel[AgentDecision]):
    """Validate and parse one discriminated agent decision."""
