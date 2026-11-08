"""Mountain-domain strategy over source-normalized, verifiable identity facts.

The ranking indicates a review priority only: exact labels outrank aliases,
but the engine must never auto-accept an entity on that preference alone.
"""

from ai_data_enrichment.resolution.engine import StrategyAssessment
from ai_data_enrichment.resolution.models import (
    CandidateAssessment,
    CandidateSet,
    ResolutionCandidate,
    ResolutionPolicy,
)


def _match_kind(candidate: ResolutionCandidate) -> str | None:
    for fact in candidate.attributes:
        if fact.name == "match_kind" and isinstance(fact.value, str):
            return fact.value
    return None


class MountainRankingStrategy:
    """Handle verified/rejected/unchecked candidates without provider P/Q IDs."""

    def assess(
        self,
        candidate_set: CandidateSet,
        policy: ResolutionPolicy,
    ) -> StrategyAssessment:
        assessments: list[CandidateAssessment] = []
        for candidate in sorted(
            candidate_set.candidates,
            key=lambda c: (c.reference.source, c.reference.entity_id),
        ):
            if candidate.verification == "rejected":
                assessments.append(
                    CandidateAssessment(
                        reference=candidate.reference,
                        eligibility="excluded",
                        reason_codes=candidate.reason_codes or ("source_rejected",),
                    )
                )
            elif candidate.verification != "verified" or candidate.entity_type != "mountain":
                assessments.append(
                    CandidateAssessment(
                        reference=candidate.reference,
                        eligibility="unchecked",
                        reason_codes=candidate.reason_codes or ("source_unverified",),
                    )
                )
            else:
                # A label is a better suggestion than an alias, not proof of
                # identity. The strategy never supplies a dominant_reference.
                is_label = _match_kind(candidate) == "label"
                rank = 1 if policy.strategy != "ranked" or is_label else 2
                assessments.append(
                    CandidateAssessment(
                        reference=candidate.reference,
                        eligibility="eligible",
                        rank=rank,
                        reason_codes=(
                            "verified_mountain_and_country",
                            "exact_label" if is_label else "exact_alias",
                        ),
                    )
                )
        return StrategyAssessment(candidates=tuple(assessments))
