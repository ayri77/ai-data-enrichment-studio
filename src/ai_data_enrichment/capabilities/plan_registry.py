"""The registry that holds a plan, from the shape of the plan.

A plan of the planner is validated and run against what the application offers; a plan the
server built for the web lookup or for the text extraction, against a registry derived from
the plan's own task. Kept apart from ``fallback`` and ``extraction`` so that neither has to
import the other.
"""

from collections.abc import Sequence

from ai_data_enrichment.capabilities.extraction import extraction_registry
from ai_data_enrichment.capabilities.fallback import fallback_registry, plan_shape
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import ColumnProfile
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan


def registry_for_plan(
    plan: EnrichmentPlan, columns: Sequence[ColumnProfile], offered: CapabilityRegistry
) -> CapabilityRegistry:
    """The registry to validate and run ``plan`` with.

    ``planner``: ``offered`` itself. ``fallback`` and ``mixed``: ``fallback_registry``, which
    holds what ``offered`` lists and the web lookup of the plan. ``extraction``:
    ``extraction_registry``, which holds the text extraction of the plan and nothing of
    ``offered``. A plan that is not well formed for its shape raises ``FallbackPlanError`` or
    ``ExtractionPlanError``.
    """
    shape = plan_shape(plan)
    if shape == "planner":
        return offered
    if shape == "extraction":
        return extraction_registry(plan, columns)
    return fallback_registry(plan, columns, offered)
