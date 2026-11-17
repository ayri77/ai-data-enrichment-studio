"""Structured results returned by the LLM planning stage."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan


class PlanningResponse(BaseModel):
    """Represent a planning decision and its optional plan."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "unsupported", "ambiguous"]
    plan: EnrichmentPlan | None
    reason: str | None
    # Requested output columns that no capability can produce. Only a ready answer may name
    # any; the plan then writes the other targets. Whether a name is a requested target is
    # checked where the targets are known (validation.plan_validator.apply_unfilled_targets).
    unfilled_targets: list[str] = Field(default_factory=list)

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

        if self.unfilled_targets:
            if self.status != "ready":
                raise ValueError("Only a ready response may list unfilled targets")

            if any(not name.strip() for name in self.unfilled_targets):
                raise ValueError("An unfilled target name must not be blank")

            if len(set(self.unfilled_targets)) != len(self.unfilled_targets):
                raise ValueError("Unfilled targets must not contain duplicates")

            # A ready answer that fills nothing is not ready.
            if self.plan is not None and (not self.plan.steps or not self.plan.outputs):
                raise ValueError(
                    "A ready response with unfilled targets must still contain "
                    "at least one step and one output"
                )

        return self
