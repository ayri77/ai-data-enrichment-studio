"""Geographic verification and conservative prominence ranking strategy.

Provider adapters supply standardized attributes; this strategy does not know
GeoNames feature codes or Wikidata properties. A population-only advantage may
order suggestions but is not sufficient for automatic acceptance.
"""

from ai_data_enrichment.execution.models import EntityRef
from ai_data_enrichment.resolution.engine import StrategyAssessment
from ai_data_enrichment.resolution.geography import administrative_area_matches
from ai_data_enrichment.resolution.models import (
    CandidateAssessment,
    CandidateSet,
    ResolutionAttribute,
    ResolutionCandidate,
    ResolutionPolicy,
    ResolutionValue,
)

_ROLE_STRENGTH = {
    "country_capital": 3,
    "administrative_seat": 2,
}
_ACCEPTED_TYPES = {"city", "settlement"}


def _attributes(attributes: tuple[ResolutionAttribute, ...]) -> dict[str, ResolutionValue]:
    return {attribute.name: attribute.value for attribute in attributes}


def _key(ref: EntityRef) -> tuple[str, str]:
    return ref.source, ref.entity_id


class GeographicRankingStrategy:
    """Apply hard country/region/type constraints before soft prominence."""

    def assess(
        self,
        candidate_set: CandidateSet,
        policy: ResolutionPolicy,
    ) -> StrategyAssessment:
        query = _attributes(candidate_set.query.context)
        expected_country = query.get("country_code")
        expected_area = query.get("administrative_area")
        if not isinstance(expected_country, str) or not expected_country.strip():
            raise ValueError("Geographic query must include a country_code.")
        if expected_area is not None and (
            not isinstance(expected_area, str) or not expected_area.strip()
        ):
            raise ValueError("Administrative area context must be text.")

        outcomes: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}
        eligible: list[ResolutionCandidate] = []

        for candidate in candidate_set.candidates:
            key = _key(candidate.reference)
            facts = _attributes(candidate.attributes)

            if candidate.verification == "rejected":
                outcomes[key] = ("excluded", ("source_rejected",))
            elif candidate.verification != "verified":
                outcomes[key] = ("unchecked", ("source_unverified",))
            elif candidate.entity_type == "location":
                outcomes[key] = ("unchecked", ("entity_type_unknown",))
            elif candidate.entity_type not in _ACCEPTED_TYPES:
                outcomes[key] = ("excluded", ("entity_type_mismatch",))
            elif not isinstance(facts.get("country_code"), str):
                outcomes[key] = ("unchecked", ("country_unverified",))
            elif facts["country_code"] != expected_country:
                outcomes[key] = ("excluded", ("country_mismatch",))
            elif expected_area is not None and not isinstance(
                facts.get("administrative_area"), str
            ):
                outcomes[key] = ("unchecked", ("administrative_area_unverified",))
            elif expected_area is not None and not administrative_area_matches(
                expected_area,
                country_code=expected_country,
                actual_admin1=str(facts["administrative_area"]),
            ):
                outcomes[key] = ("excluded", ("administrative_area_mismatch",))
            else:
                eligible.append(candidate)
                outcomes[key] = ("eligible", ("verified_context_match",))

        ranks: dict[tuple[str, str], int] = {}
        prominent = policy.strategy == "ranked" and policy.prefer_prominent
        if not prominent:
            for candidate in eligible:
                ranks[_key(candidate.reference)] = 1
        else:
            rank_counter = 1
            tiers = sorted(
                {_role_strength(candidate) for candidate in eligible}, reverse=True
            )
            for tier in tiers:
                group = [c for c in eligible if _role_strength(c) == tier]
                populations = [_population(c) for c in group]
                if len(group) > 1 and all(value is not None for value in populations):
                    # All values in a comparable peer group are known.
                    # Ranking is not a calibrated probability.
                    for population in sorted(set(populations), reverse=True):
                        for candidate in group:
                            if _population(candidate) == population:
                                ranks[_key(candidate.reference)] = rank_counter
                        rank_counter += 1
                else:
                    for candidate in group:
                        ranks[_key(candidate.reference)] = rank_counter
                    rank_counter += 1

        assessed: list[CandidateAssessment] = []
        for candidate in sorted(candidate_set.candidates, key=lambda c: _key(c.reference)):
            key = _key(candidate.reference)
            eligibility, reasons = outcomes[key]
            if eligibility == "eligible" and prominent:
                tier = _role_strength(candidate)
                if tier == 3:
                    reasons += ("country_capital_role",)
                elif tier == 2:
                    reasons += ("administrative_seat_role",)
                else:
                    reasons += ("ordinary_settlement",)
                if _population(candidate) is not None:
                    reasons += ("population_recorded",)
            assessed.append(
                CandidateAssessment(
                    reference=candidate.reference,
                    eligibility=eligibility,  # type: ignore[arg-type]
                    rank=ranks.get(key),
                    reason_codes=reasons,
                )
            )

        dominant: EntityRef | None = None
        if prominent and len(eligible) > 1:
            strongest = max(_role_strength(c) for c in eligible)
            leaders = [c for c in eligible if _role_strength(c) == strongest]
            if strongest >= 2 and len(leaders) == 1:
                dominant = leaders[0].reference

        return StrategyAssessment(candidates=tuple(assessed), dominant_reference=dominant)


def _role_strength(candidate: ResolutionCandidate) -> int:
    role = _attributes(candidate.attributes).get("administrative_role")
    return _ROLE_STRENGTH.get(role, 1) if isinstance(role, str) else 1


def _population(candidate: ResolutionCandidate) -> int | None:
    population = _attributes(candidate.attributes).get("population")
    if type(population) is int and population >= 0:
        return population
    return None
