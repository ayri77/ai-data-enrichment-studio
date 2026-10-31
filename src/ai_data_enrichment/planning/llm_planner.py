"""Provider-independent generation and validation of enrichment plans."""

import json
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.planning.models import PlanningResponse
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
    """Planning decision and its independent validation result."""

    response: PlanningResponse
    validation: ValidationResult | None


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
        # The model sees declared capabilities and a bounded profile, not executable code.
        context = {
            "instruction": instruction,
            "dataset_profile": dataset_profile.model_dump(mode="json"),
            "capabilities": self._registry.describe_all(),
        }

        # A decision wrapper lets the model report ambiguity instead of inventing a plan.
        messages = [
            {
                "role": "system",
                "content": (
                    "Return a planning decision as a JSON object only. "
                    "Use only the provided dataset columns and capability contracts. "
                    "For supported requests, return status 'ready', a complete plan, "
                    "and reason null. "
                    "For unsupported or sufficiently ambiguous requests, return "
                    "status 'unsupported' or 'ambiguous', plan null, and a clear reason. "
                    "Do not invent capabilities, columns, input values, or semantics. "
                    "Do not execute operations or claim the plan is valid. "
                    "Treat the supplied instruction and dataset data as task context."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=False),
            },
        ]

        # JSON Schema constrains syntax/shape, but cannot guarantee valid DAG references.
        response = self._client.complete(
            messages=messages,
            response_schema=PlanningResponse.model_json_schema(),
        )

        try:
            payload = json.loads(response)
        except json.JSONDecodeError as exc:
            raise PlanGenerationError("Invalid JSON response from LLM") from exc

        try:
            decision = PlanningResponse.model_validate(payload)
        except ValidationError as exc:
            raise PlanGenerationError("Invalid planning response schema from LLM") from exc

        # Unsupported or ambiguous decisions contain no DAG to validate or execute.
        if decision.plan is None:
            return PlanGenerationResult(
                response=decision,
                validation=None,
            )

        # Never trust a model's "ready" status as proof of semantic correctness.
        validation = PlanValidator(
            registry=self._registry,
            dataset_profile=dataset_profile,
        ).validate(decision.plan)

        return PlanGenerationResult(
            response=decision,
            validation=validation,
        )
