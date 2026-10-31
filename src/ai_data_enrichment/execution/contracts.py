"""Interfaces for executing registered capabilities."""

from collections.abc import Mapping
from typing import Protocol

from ai_data_enrichment.execution.models import (
    OperationResult,
    RuntimeValue,
)


class CapabilityHandler(Protocol):
    """Execute a capability using resolved runtime inputs."""

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        """Return a structured success or failure outcome."""
        ...
