"""Tests for structured LLM planning responses."""

from typing import Literal

import pytest
from pydantic import ValidationError

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.planning.models import PlanningResponse


@pytest.mark.parametrize("status", ["unsupported", "ambiguous"])
def test_planning_response_represents_failure(
    status: Literal["unsupported", "ambiguous"],
) -> None:
    """Represent an unplannable request with an explicit reason."""

    response = PlanningResponse(
        status=status,
        plan=None,
        reason="The request cannot be planned reliably.",
    )

    assert response.status == status
    assert response.plan is None
    assert response.reason


@pytest.mark.parametrize("reason", [None, "", "  "])
def test_planning_response_requires_failure_reason(
    reason: str | None,
) -> None:
    """Reject planning failures without a meaningful explanation."""

    with pytest.raises(ValidationError):
        PlanningResponse(
            status="unsupported",
            plan=None,
            reason=reason,
        )


def test_planning_response_requires_plan_when_ready() -> None:
    """Reject successful planning decisions without a plan."""

    with pytest.raises(ValidationError):
        PlanningResponse(
            status="ready",
            plan=None,
            reason=None,
        )


def test_planning_response_accepts_ready_with_plan() -> None:
    """Accept a ready decision containing a structured plan."""

    plan = EnrichmentPlan(
        version="1.0",
        steps=[],
        outputs=[],
        assumptions=[],
    )

    response = PlanningResponse(
        status="ready",
        plan=plan,
        reason=None,
    )

    assert response.status == "ready"
    assert response.plan == plan
    assert response.reason is None


@pytest.mark.parametrize("status", ["unsupported", "ambiguous"])
def test_planning_response_rejects_failure_with_plan(
    status: Literal["unsupported", "ambiguous"],
) -> None:
    """Reject failed planning decisions containing a plan."""

    plan = EnrichmentPlan(
        version="1.0",
        steps=[],
        outputs=[],
        assumptions=[],
    )

    with pytest.raises(
        ValidationError,
        match="A failed planning response must not contain a plan",
    ):
        PlanningResponse(
            status=status,
            plan=plan,
            reason="The request cannot be planned reliably.",
        )


def test_planning_response_rejects_ready_with_reason() -> None:
    """Reject successful planning decisions containing a failure reason."""

    plan = EnrichmentPlan(
        version="1.0",
        steps=[],
        outputs=[],
        assumptions=[],
    )

    with pytest.raises(ValidationError):
        PlanningResponse(
            status="ready",
            plan=plan,
            reason="The request is unsupported.",
        )
