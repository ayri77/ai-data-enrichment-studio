"""Provider-independent, abstaining resolution decisions over typed candidate sets.

The engine only consumes verified attributes, strategy assessments and a
versioned policy. It does not execute provider requests or infer source facts.
"""

from dataclasses import dataclass
from typing import Protocol

from ai_data_enrichment.execution.models import EntityRef
from ai_data_enrichment.resolution.models import (
    CandidateAssessment,
    CandidateSet,
    ResolutionDecision,
    ResolutionPolicy,
    ResolutionReport,
)


@dataclass(frozen=True)
class StrategyAssessment:
    """Explanations and any *categorically justified* automatic preference."""

    candidates: tuple[CandidateAssessment, ...]
    dominant_reference: EntityRef | None = None


class ResolutionStrategy(Protocol):
    """Assess domain-specific candidates without producing a final decision."""

    def assess(
        self,
        candidate_set: CandidateSet,
        policy: ResolutionPolicy,
    ) -> StrategyAssessment:
        """Return one auditable assessment for each input candidate."""
        ...


def _reference_key(ref: EntityRef) -> tuple[str, str]:
    return ref.source, ref.entity_id


class ResolutionEngine:
    """Apply common acceptance and abstention rules to pluggable assessments."""

    def resolve(
        self,
        *,
        candidate_set: CandidateSet,
        policy: ResolutionPolicy,
        strategy: ResolutionStrategy,
    ) -> ResolutionReport:
        """Select only verified candidates under the policy's completeness rules."""
        ranked = strategy.assess(candidate_set, policy)
        expected = {_reference_key(c.reference) for c in candidate_set.candidates}
        actual = [_reference_key(a.reference) for a in ranked.candidates]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError("Strategy must assess every candidate exactly once.")

        by_id = {_reference_key(c.reference): c for c in candidate_set.candidates}
        for assessment in ranked.candidates:
            candidate = by_id[_reference_key(assessment.reference)]
            if assessment.eligibility == "eligible" and candidate.verification != "verified":
                raise ValueError("Strategy cannot accept an unverified source candidate.")

        eligible = [
            assessment for assessment in ranked.candidates if assessment.eligibility == "eligible"
        ]
        unchecked = [
            assessment for assessment in ranked.candidates if assessment.eligibility == "unchecked"
        ]
        complete = candidate_set.search_complete and candidate_set.verification_complete

        if candidate_set.provider_error_code is not None:
            decision = ResolutionDecision(
                status="provider_failure",
                reason_codes=("source_failure",),
                explanation="The data provider failed before a definitive decision.",
            )
        elif policy.strategy == "verified_only" and candidate_set.search_complete and eligible:
            decision = (
                ResolutionDecision(
                    status="resolved",
                    selected=eligible[0].reference,
                    basis="verified_only",
                    reason_codes=("single_positively_verified_candidate",)
                    + (("unchecked_alternatives_remain",) if unchecked else ()),
                    explanation=(
                        "Selected the only positively verified eligible candidate under "
                        "verified_only policy."
                    )
                    + (" Unchecked alternatives remain uncertain." if unchecked else ""),
                )
                if len(eligible) == 1
                else ResolutionDecision(
                    status="needs_review",
                    reason_codes=("multiple_positively_verified_candidates",),
                    explanation="Several positively verified candidates satisfy the context.",
                )
            )
        elif not complete:
            decision = ResolutionDecision(
                status="incomplete_verification",
                reason_codes=("source_incomplete",),
                explanation="Candidate search or verification was not completed.",
            )
        elif not eligible and not unchecked:
            decision = ResolutionDecision(
                status="unresolved",
                reason_codes=("no_eligible_candidate",),
                explanation="No source candidate satisfied all required constraints.",
            )
        elif len(eligible) == 1 and not unchecked:
            decision = ResolutionDecision(
                status="resolved",
                selected=eligible[0].reference,
                basis="unique_verified",
                reason_codes=("unique_eligible_candidate",),
                explanation="Exactly one verified candidate satisfies the required context.",
            )
        else:
            winners = [item for item in eligible if item.rank == 1]
            auto_select = (
                policy.strategy == "ranked"
                and policy.auto_accept_ranked
                and policy.prefer_prominent
                and not unchecked
                and ranked.dominant_reference is not None
                and len(winners) == 1
                and winners[0].reference == ranked.dominant_reference
            )
            decision = (
                ResolutionDecision(
                    status="auto_resolved",
                    selected=winners[0].reference,
                    basis="ranking_policy",
                    reason_codes=("decisive_prominence",),
                    explanation="One verified candidate has a distinct administrative role.",
                )
                if auto_select
                else ResolutionDecision(
                    status="needs_review",
                    reason_codes=("ambiguous_or_unverified",),
                    explanation="Several plausible candidates or unverifiable context remain.",
                )
            )

        # The report validator independently checks selected identity and policy.
        return ResolutionReport(
            candidate_set=candidate_set,
            policy=policy,
            assessments=ranked.candidates,
            decision=decision,
        )
