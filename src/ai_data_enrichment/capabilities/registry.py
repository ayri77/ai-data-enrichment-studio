"""Registry for managing available enrichment capabilities."""

from typing import Any

from ai_data_enrichment.domain.capability import CapabilitySpec


class CapabilityRegistry:
    """Registry for managing available enrichment capabilities."""

    def __init__(self) -> None:
        """Initialize an empty capability registry."""
        self._capabilities: dict[str, CapabilitySpec] = {}

    def get(self, capability_id: str) -> CapabilitySpec | None:
        """Return a registered capability by ID, or None if not found."""
        return self._capabilities.get(capability_id)

    def register(self, capability: CapabilitySpec) -> None:
        """Register a new capability."""
        if capability.id in self._capabilities:
            raise ValueError(f"Capability with ID '{capability.id}' already registered")
        self._capabilities[capability.id] = capability

    def list_all(self) -> list[CapabilitySpec]:
        """Return all registered capability definitions."""
        return list(self._capabilities.values())

    def describe_all(self) -> list[dict[str, Any]]:
        """Return JSON-compatible capability descriptions for the planner."""
        return [capability.model_dump(mode="json") for capability in self._capabilities.values()]
