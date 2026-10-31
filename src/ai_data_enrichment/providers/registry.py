"""Registry for approved capability provider implementations."""

from collections.abc import Iterable, Mapping

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.execution.contracts import CapabilityHandler


class ProviderRegistry:
    """Map registered capabilities to approved provider handlers."""

    def __init__(self, capabilities: CapabilityRegistry) -> None:
        self._capabilities = capabilities
        self._handlers: dict[tuple[str, str], CapabilityHandler] = {}

    def register(
        self,
        *,
        capability_id: str,
        provider_id: str,
        handler: CapabilityHandler,
    ) -> None:
        """Register an approved handler for a known capability."""
        if self._capabilities.get(capability_id) is None:
            raise ValueError(f"Unknown capability '{capability_id}'.")

        if not provider_id.strip():
            raise ValueError("Provider ID must not be empty.")

        if not callable(getattr(handler, "execute", None)):
            raise TypeError("Provider handler must implement execute.")

        key = (capability_id, provider_id)

        if key in self._handlers:
            raise ValueError(
                f"Provider '{provider_id}' already registered for capability '{capability_id}'."
            )

        self._handlers[key] = handler

    def get(
        self,
        *,
        capability_id: str,
        provider_id: str,
    ) -> CapabilityHandler | None:
        """Return the registered handler, if available."""
        return self._handlers.get((capability_id, provider_id))

    def resolve_handlers(
        self,
        *,
        capability_ids: Iterable[str],
        selected_providers: Mapping[str, str],
    ) -> dict[str, CapabilityHandler]:
        """Resolve approved handlers before starting execution."""
        resolved: dict[str, CapabilityHandler] = {}

        for capability_id in sorted(set(capability_ids)):
            provider_id = selected_providers.get(capability_id)

            if provider_id is None:
                raise ValueError(f"No provider selected for capability '{capability_id}'.")

            handler = self.get(
                capability_id=capability_id,
                provider_id=provider_id,
            )

            if handler is None:
                raise ValueError(
                    f"Provider '{provider_id}' is not registered for capability '{capability_id}'."
                )

            resolved[capability_id] = handler

        return resolved
