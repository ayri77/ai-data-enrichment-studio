"""Normalize bounded Wikidata search hits without claiming entity verification.

The resolver in #64 will separately verify P31/P279 types and P17 countries.
A search match or a first-position QID is never sufficient Evidence of
mountain identity.
"""

import re
from collections.abc import Sequence

from ai_data_enrichment.execution.models import EntityRef
from ai_data_enrichment.providers.wikidata_constants import (
    WIKIDATA_MAX_SEARCH_PAGES,
    WIKIDATA_SEARCH_PAGE_SIZE,
)
from ai_data_enrichment.providers.wikidata_parsing import (
    _candidate_matches_name,
    _normalize_entity_name,
)
from ai_data_enrichment.resolution.models import (
    CandidateSet,
    ResolutionAttribute,
    ResolutionCandidate,
    ResolutionQuery,
)


def normalize_wikidata_search_candidates(
    search_items: Sequence[object],
    *,
    name: str,
    country_code: str,
    search_complete: bool,
    max_items: int = WIKIDATA_SEARCH_PAGE_SIZE * WIKIDATA_MAX_SEARCH_PAGES,
) -> CandidateSet:
    """Represent exact label/alias matches as *unchecked* Wikidata candidates."""
    if not name.strip() or re.fullmatch(r"[A-Z]{2}", country_code) is None:
        raise ValueError("Mountain name and ISO country code are required.")
    if max_items < 1 or len(search_items) > max_items:
        raise ValueError("Wikidata search results exceed the configured bound.")

    candidates: list[ResolutionCandidate] = []
    seen_ids: set[str] = set()

    for raw in search_items:
        if not isinstance(raw, dict):
            raise ValueError("Wikidata search item must be an object.")
        if not _candidate_matches_name(raw, name):
            continue

        qid = raw.get("id")
        if not isinstance(qid, str) or re.fullmatch(r"Q[1-9]\d*", qid) is None:
            raise ValueError("Matched Wikidata candidate has an invalid entity ID.")
        if qid in seen_ids:
            continue
        seen_ids.add(qid)

        label = raw.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("Matched Wikidata candidate has no valid label.")

        matched_by = (
            "label"
            if _normalize_entity_name(label) == _normalize_entity_name(name)
            else "alias"
        )
        candidates.append(
            ResolutionCandidate(
                reference=EntityRef(source="wikidata", entity_id=qid),
                label=label.strip(),
                entity_type="unknown",
                verification="unchecked",
                attributes=(
                    ResolutionAttribute(name="match_kind", value=matched_by),
                ),
                reason_codes=("search_match_only",),
            )
        )

    # Search completeness and candidate *identity verification* are different.
    verified = search_complete and not candidates
    return CandidateSet(
        provider_id="wikidata",
        query=ResolutionQuery(
            name=name.strip(),
            entity_type="mountain",
            context=(ResolutionAttribute(name="country_code", value=country_code),),
        ),
        candidates=tuple(candidates),
        search_complete=search_complete,
        verification_complete=verified,
        incomplete_reason=(
            None
            if verified
            else "candidate_verification_pending"
            if search_complete
            else "search_pages_incomplete"
        ),
    )
