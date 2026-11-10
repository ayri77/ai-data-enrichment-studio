"""Bounded explicit-text policies, not a proof of general semantic equivalence.

Only the user's instruction supplies policy evidence. Headers, samples and model
assumptions cannot authorize constraints. Extend the rule table for new supported
wording/capabilities; unrecognized language still needs human plan review.
"""

import re
from dataclasses import dataclass
from typing import Literal

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.validation.models import ValidationDiagnostic


@dataclass(frozen=True)
class ParameterConstraint:
    """An instruction-backed requirement or prohibition on a capability parameter."""

    capability: str
    parameter: str
    value: str
    mode: Literal["forbidden", "required"]
    evidence: str


# Deliberately small English vocabulary for explicit, global city-role instructions.
# Do not match generic mentions of capitals or infer policy from column names.
_LITERAL_CITY = re.compile(
    r"\b(?:literal[ -]cit(?:y|ies)(?: names)?|not necessarily (?:country )?capitals|"
    r"regardless of (?:their )?administrative role)\b",
    re.IGNORECASE,
)
_NO_CAPITAL_ROLE = re.compile(
    r"\b(?:do not require (?:country[ -])?capital status|"
    r"do not set required_role(?:\s*=\s*[\"']?country_capital[\"']?)?)\b",
    re.IGNORECASE,
)
_REQUIRE_CAPITAL_ROLE = re.compile(
    r"(?:^|[.;!?]\s*|\band\s+)(?:please\s+)?(?:require country[ -]capital status|"
    r"(?:use |accept )?only (?:use |accept )?country capitals)\b",
    re.IGNORECASE,
)


def constraints_from_instruction(instruction: str) -> tuple[ParameterConstraint, ...]:
    """Recognize documented global phrases; an explicit role request qualifies literal lookup."""
    constraints = []
    required = _REQUIRE_CAPITAL_ROLE.search(instruction)
    prohibited = _NO_CAPITAL_ROLE.search(instruction)
    literal = _LITERAL_CITY.search(instruction)
    # Literal names and explicitly required capital status can coexist. An explicit
    # prohibition plus an explicit requirement is conflicting user context: retain
    # both policies and let the planner abstain, never guess which one to discard.
    if prohibited or (literal and not required):
        match = prohibited or literal
        assert match is not None
        constraints.append(
            ParameterConstraint(
                "geocode", "required_role", "country_capital", "forbidden", match.group()
            )
        )
    if required:
        constraints.append(
            ParameterConstraint(
                "geocode", "required_role", "country_capital", "required", required.group().strip()
            )
        )
    return tuple(constraints)


def validate_intent(
    plan: EnrichmentPlan, constraints: tuple[ParameterConstraint, ...]
) -> list[ValidationDiagnostic]:
    """Check each applicable step without mutating the proposal or trusting its claims."""
    errors = []
    for constraint in constraints:
        for step in plan.steps:
            if step.capability != constraint.capability:
                continue
            matches = step.parameters.get(constraint.parameter) == constraint.value
            if matches == (constraint.mode == "required"):
                continue
            action = "prohibits" if constraint.mode == "forbidden" else "requires"
            errors.append(
                ValidationDiagnostic(
                    category="intent",
                    code="INTENT_PARAMETER_CONFLICT",
                    message=(
                        f"Explicit user intent {action} {constraint.capability}."
                        f"{constraint.parameter}={constraint.value}. "
                        "Propose a corrected plan or abstain if the instruction conflicts. "
                        "For unrestricted city lookup, omit the optional role parameter; "
                        "do not set it to null."
                    ),
                    step_id=step.id,
                    field=f"parameters.{constraint.parameter}",
                )
            )
    return errors
