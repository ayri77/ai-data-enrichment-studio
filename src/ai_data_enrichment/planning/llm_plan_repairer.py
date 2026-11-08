"""LLM-based repair of semantically invalid enrichment plans."""

import json

from pydantic import ValidationError

from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.planning.llm_planner import PlanCompletionClient
from ai_data_enrichment.validation.models import ValidationResult


class LLMPlanRepairer:
    """Propose a corrected plan using validator diagnostics."""

    def __init__(
        self,
        *,
        client: PlanCompletionClient,
        registry: CapabilityRegistry,
    ) -> None:
        self._client = client
        self._registry = registry

    def repair(
        self,
        *,
        plan: EnrichmentPlan,
        validation: ValidationResult,
        dataset_profile: DatasetProfile,
        instruction: str | None,
    ) -> EnrichmentPlan:
        """Request one repair proposal; semantic validation stays in the graph."""

        if validation.valid:
            raise ValueError("Cannot repair a plan without validation errors.")

        context = {
            "instruction": instruction,
            "dataset_profile": dataset_profile.model_dump(mode="json"),
            "capabilities": self._registry.describe_all(),
            "invalid_plan": plan.model_dump(mode="json"),
            "validation_errors": [error.model_dump(mode="json") for error in validation.errors],
        }

        messages = [
            {
                "role": "system",
                "content": (
                    "You repair declarative enrichment plans. "
                    "Return a complete corrected EnrichmentPlan JSON object. "
                    "Use only registered capabilities and existing dataset columns. "
                    "Fix the supplied deterministic validation errors. "
                    "Preserve the original user intent and valid plan structure. "
                    "Do not invent data, capabilities, or column names. "
                    "Do not execute operations. "
                    "Do not claim the corrected plan has passed validation."
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
            raise ValueError("Repair response is not valid JSON.") from exc

        try:
            return EnrichmentPlan.model_validate(payload)
        except ValidationError as exc:
            raise ValueError("Repair response does not match EnrichmentPlan schema.") from exc
