"""Validate autonomous agent tool requests before execution."""

from collections.abc import Mapping

from ai_data_enrichment.capabilities.registry import CapabilityRegistry

from .contracts import ToolCall, ToolObservation


def validate_tool_call(
    *,
    call: ToolCall,
    registry: CapabilityRegistry,
    allowed_capabilities: frozenset[str],
    source_columns: frozenset[str],
    observations: Mapping[str, ToolObservation],
) -> None:
    """Validate whether the agent may execute a requested tool."""

    # The agent may use only explicitly approved capabilities.
    if call.capability_id not in allowed_capabilities:
        raise ValueError(f"Capability '{call.capability_id}' is not allowed.")

    # An approved name must also refer to a registered capability.
    if registry.get(call.capability_id) is None:
        raise ValueError(f"Capability '{call.capability_id}' is not registered.")
