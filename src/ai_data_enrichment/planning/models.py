"""Structured results returned by the LLM planning stage."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan


class PlanningResponse(BaseModel):
    """Represent a planning decision and its optional plan."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "unsupported", "ambiguous"]
    plan: EnrichmentPlan | None
    reason: str | None

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        """Ensure the planning status agrees with the plan and reason."""

        if self.status == "ready":
            if self.plan is None:
                raise ValueError("A ready response must contain a plan")

            if self.reason is not None:
                raise ValueError("A ready response must not contain a reason")

        else:
            if self.plan is not None:
                raise ValueError("A failed planning response must not contain a plan")

            if self.reason is None or not self.reason.strip():
                raise ValueError("A failed planning response must explain the reason")

        return self
