"""Minimal autonomous tool-execution loop."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.execution.contracts import CapabilityHandler
from ai_data_enrichment.execution.job_models import InputRecord
from ai_data_enrichment.execution.models import RuntimeValue

from .contracts import (
    Abstain,
    AgentDecision,
    Finish,
    ObservationRef,
    SourceFieldRef,
    ToolCall,
    ToolObservation,
)
from .validation import validate_tool_call


class DecisionMaker(Protocol):
    """Choose the next action from the current observations."""

    def decide(
        self,
        *,
        instruction: str,
        record: InputRecord,
        observations: tuple[ToolObservation, ...],
    ) -> AgentDecision: ...


@dataclass(frozen=True)
class AgentRunResult:
    """Outcome and observable history of one agent run."""

    status: Literal["completed", "abstained", "limit_reached"]
    observations: tuple[ToolObservation, ...]
    value: RuntimeValue | None = None
    result_call_id: str | None = None
    reason: str | None = None


class AutonomousRunner:
    """Execute dynamically selected, registered capabilities."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        handlers: Mapping[str, CapabilityHandler],
        allowed_capabilities: frozenset[str],
        decision_maker: DecisionMaker,
        max_decisions: int = 8,
    ) -> None:
        if max_decisions < 1:
            raise ValueError("max_decisions must be positive.")

        self.registry = registry
        self.handlers = dict(handlers)
        self.allowed_capabilities = allowed_capabilities
        self.decision_maker = decision_maker
        self.max_decisions = max_decisions

    def run(
        self,
        *,
        instruction: str,
        record: InputRecord,
    ) -> AgentRunResult:
        """Run one record until finish, abstain, or decision limit."""

        observations: list[ToolObservation] = []
        by_id: dict[str, ToolObservation] = {}

        for _ in range(self.max_decisions):
            decision = self.decision_maker.decide(
                instruction=instruction,
                record=record,
                observations=tuple(observations),
            )

            if isinstance(decision, Abstain):
                return AgentRunResult(
                    status="abstained",
                    observations=tuple(observations),
                    reason=decision.message,
                )

            if isinstance(decision, Finish):
                previous = by_id.get(decision.result_call_id)

                if (
                    previous is None
                    or previous.result.status != "success"
                    or previous.result.value is None
                ):
                    raise ValueError("Finish must reference a successful tool call.")

                return AgentRunResult(
                    status="completed",
                    observations=tuple(observations),
                    value=previous.result.value,
                    result_call_id=decision.result_call_id,
                )

            assert isinstance(decision, ToolCall)

            validate_tool_call(
                call=decision,
                registry=self.registry,
                allowed_capabilities=self.allowed_capabilities,
                source_columns=frozenset(record.values),
                observations=by_id,
            )

            if decision.call_id in by_id:
                raise ValueError("Duplicate tool call ID.")

            handler = self.handlers.get(decision.capability_id)
            if handler is None:
                raise ValueError("No handler configured for capability.")

            inputs: dict[str, RuntimeValue] = {}

            for name, ref in decision.inputs.items():
                if isinstance(ref, SourceFieldRef):
                    value = record.values.get(ref.column)

                    if value is None:
                        raise ValueError(f"Missing source value: {ref.column}")

                elif isinstance(ref, ObservationRef):
                    previous = by_id.get(ref.call_id)

                    if (
                        previous is None
                        or previous.result.status != "success"
                        or previous.result.value is None
                    ):
                        raise ValueError(f"Unavailable observation: {ref.call_id}")

                    value = previous.result.value

                else:
                    raise ValueError("Unsupported tool input reference.")

                inputs[name] = value

            result = handler.execute(
                inputs=inputs,
                parameters=decision.parameters,
            )

            observation = ToolObservation(
                call_id=decision.call_id,
                capability_id=decision.capability_id,
                result=result,
            )

            observations.append(observation)
            by_id[decision.call_id] = observation

        return AgentRunResult(
            status="limit_reached",
            observations=tuple(observations),
            reason="Maximum number of decisions reached.",
        )
