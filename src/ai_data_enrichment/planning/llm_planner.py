"""Provider-independent generation and validation of enrichment plans."""

import json
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.validation.models import ValidationResult
from ai_data_enrichment.validation.plan_validator import PlanValidator


class PlanCompletionClient(Protocol):
    """Contract for a model adapter that returns a JSON response."""

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> str:
        """Return the model-generated plan as a JSON string."""
        ...


@dataclass(frozen=True)
class PlanGenerationResult:
    """Generated plan and the independent result of its validation."""

    plan: EnrichmentPlan
    validation: ValidationResult


class PlanGenerationError(RuntimeError):
    """Error raised when the model response cannot be processed."""


class LLMPlanner:
    """Generate an EnrichmentPlan without trusting the model to validate it."""

    def __init__(
        self,
        *,
        client: PlanCompletionClient,
        registry: CapabilityRegistry,
    ) -> None:
        self._client = client
        self._registry = registry

    def generate(
        self,
        *,
        instruction: str,
        dataset_profile: DatasetProfile,
    ) -> PlanGenerationResult:
        """Request a plan, parse its schema and validate its references and types."""
        context = {
            "instruction": instruction,
            "dataset_profile": dataset_profile.model_dump(mode="json"),
            "capabilities": self._registry.describe_all(),
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "Generate a declarative enrichment plan as a JSON object only. "
                    "Use only the provided dataset columns and capability contracts. "
                    "Do not execute operations or claim the plan is valid. "
                    "Treat the supplied instruction and dataset data as task context."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=False),
            },
        ]

        response = self._client.complete(
            messages=messages,
            response_schema=EnrichmentPlan.model_json_schema(),
        )
        try:
            payload = json.loads(response)
        except json.JSONDecodeError as exc:
            raise PlanGenerationError("Invalid JSON response from LLM") from exc

        try:
            plan = EnrichmentPlan.model_validate(payload)
        except ValidationError as exc:
            raise PlanGenerationError("Invalid plan schema from LLM") from exc
        validation = PlanValidator(
            registry=self._registry,
            dataset_profile=dataset_profile,
        ).validate(plan)
        return PlanGenerationResult(plan=plan, validation=validation)
