"""Opt-in bounded Wikidata mountain resolution with complete decision provenance.

The strict CapabilityHandler remains compatible. This separate service returns
OperationResult and ResolutionReport as an inseparable pair until #65 integrates
audited resolution with job memoization and per-row review references.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import pycountry
from pydantic import ValidationError

from ai_data_enrichment.execution.models import Evidence, OperationResult, RuntimeValue
from ai_data_enrichment.providers.http_transport import JsonTransport
from ai_data_enrichment.providers.wikidata_candidates import normalize_wikidata_search_candidates
from ai_data_enrichment.providers.wikidata_constants import (
    WIKIDATA_MAX_VERIFY_CANDIDATES,
)
from ai_data_enrichment.providers.wikidata_search import fetch_wikidata_search
from ai_data_enrichment.providers.wikidata_verification import verify_wikidata_mountain_candidate
from ai_data_enrichment.resolution.engine import ResolutionEngine
from ai_data_enrichment.resolution.models import (
    CandidateSet,
    ResolutionAttribute,
    ResolutionCandidate,
    ResolutionPolicy,
    ResolutionQuery,
    ResolutionReport,
)
from ai_data_enrichment.resolution.mountain import MountainRankingStrategy

_EXCLUDED_REASON_CODES = {
    "Candidate has an explicitly non-mountain entity type.": "direct_non_mountain_class",
    "Candidate is not classified as a mountain.": "no_mountain_class_path",
    "Wikidata candidate entity does not exist.": "missing_source_entity",
}


@dataclass(frozen=True)
class WikidataResolutionOutcome:
    """One operation result paired with an immutable candidate/decision audit."""

    operation: OperationResult
    report: ResolutionReport


class WikidataMountainResolutionService:
    """Validate original query, search bounded pages and verify bounded candidates."""

    def __init__(
        self,
        transport: JsonTransport,
        *,
        max_verify_candidates: int = WIKIDATA_MAX_VERIFY_CANDIDATES,
    ) -> None:
        if not 1 <= max_verify_candidates <= WIKIDATA_MAX_VERIFY_CANDIDATES:
            raise ValueError("Mountain candidate verification budget is outside application limits.")
        self._transport = transport
        self._max_verify_candidates = max_verify_candidates
        self._engine = ResolutionEngine()
        self._strategy = MountainRankingStrategy()

    def resolve(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        policy: ResolutionPolicy,
    ) -> WikidataResolutionOutcome:
        """Return a verifiable result or abstain with observed alternatives."""
        if set(inputs) - {"name", "country"}:
            raise ValueError("Unregistered Wikidata resolution input.")
        name = inputs.get("name")
        country = inputs.get("country")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Resolution requires a nonblank mountain name.")
        if not isinstance(country, str) or not country.strip():
            raise ValueError("Resolution requires a nonblank country.")
        try:
            country_code = pycountry.countries.lookup(country.strip()).alpha_2
        except LookupError as exc:
            raise ValueError("Resolution requires a recognized country.") from exc

        query = ResolutionQuery(
            name=name.strip(),
            entity_type="mountain",
            context=(ResolutionAttribute(name="country_code", value=country_code),),
        )
        search = fetch_wikidata_search(self._transport, name=name)
        try:
            normalized = normalize_wikidata_search_candidates(
                search.items,
                name=name.strip(),
                country_code=country_code,
                search_complete=search.complete,
            )
        except (ValueError, ValidationError):
            return self._provider_failure(
                query=query,
                policy=policy,
                error=OperationResult(
                    status="failure",
                    error_code="INVALID_RESPONSE",
                    message="Wikidata search results contain invalid candidate data.",
                ),
            )

        if search.failure is not None:
            return self._provider_failure(
                query=query,
                policy=policy,
                error=search.failure,
                candidates=normalized.candidates,
            )

        if not search.complete:
            return self._emit(
                candidate_set=normalized,
                policy=policy,
            )

        replacements: dict[str, ResolutionCandidate] = {}
        failure: OperationResult | None = None
        ordered = sorted(
            normalized.candidates,
            key=lambda c: (
                _match_kind(c) != "label",
                c.reference.entity_id,
            ),
        )

        for candidate in ordered[: self._max_verify_candidates]:
            qid = candidate.reference.entity_id
            checked = verify_wikidata_mountain_candidate(
                self._transport,
                qid=qid,
                expected_country_code=country_code,
            )
            if checked.status == "success":
                replacements[qid] = candidate.model_copy(
                    update={
                        "entity_type": "mountain",
                        "verification": "verified",
                        "attributes": (
                            *candidate.attributes,
                            ResolutionAttribute(
                                name="country_code",
                                value=country_code,
                                evidence=checked.evidence,
                            ),
                        ),
                        "evidence": checked.evidence,
                        "reason_codes": ("source_mountain_type_and_country_verified",),
                    }
                )
            elif checked.error_code == "NOT_FOUND":
                excluded = _EXCLUDED_REASON_CODES.get(checked.message or "")
                if excluded is not None:
                    replacements[qid] = candidate.model_copy(
                        update={
                            "entity_type": "non_mountain" if excluded != "missing_source_entity"
                            else "unknown",
                            "verification": "rejected",
                            "reason_codes": (excluded,),
                            "evidence": (
                                Evidence(source="wikidata", reference=f"{qid}:P31"),
                            ) if excluded != "missing_source_entity" else (),
                        }
                    )
                else:
                    replacements[qid] = candidate.model_copy(
                        update={"reason_codes": ("country_or_type_unverified",)}
                    )
            elif checked.error_code in {"INCOMPLETE_HIERARCHY", "INCOMPLETE_VERIFICATION"}:
                replacements[qid] = candidate.model_copy(
                    update={"reason_codes": ("hierarchy_verification_incomplete",)}
                )
            else:
                failure = checked
                replacements[qid] = candidate.model_copy(
                    update={"reason_codes": ("provider_verification_error",)}
                )
                break

        candidates = tuple(
            replacements.get(candidate.reference.entity_id, candidate)
            for candidate in normalized.candidates
        )
        complete = all(candidate.verification != "unchecked" for candidate in candidates)

        if failure is not None:
            if failure.error_code is None:
                raise ValueError("Provider failure requires an error code.")
            candidate_set = CandidateSet(
                query=query,
                provider_id="wikidata",
                candidates=candidates,
                search_complete=True,
                verification_complete=False,
                provider_error_code=failure.error_code,
                incomplete_reason="provider_verification_error",
            )
            return self._emit(candidate_set=candidate_set, policy=policy, failure=failure)

        exceeded = len(ordered) > self._max_verify_candidates
        return self._emit(
            candidate_set=CandidateSet(
                query=query,
                provider_id="wikidata",
                candidates=candidates,
                search_complete=True,
                verification_complete=complete,
                incomplete_reason=(
                    None
                    if complete
                    else "candidate_verification_budget_exceeded"
                    if exceeded
                    else "candidate_verification_incomplete"
                ),
            ),
            policy=policy,
        )

    def _provider_failure(
        self,
        *,
        query: ResolutionQuery,
        policy: ResolutionPolicy,
        error: OperationResult,
        candidates: tuple[ResolutionCandidate, ...] = (),
    ) -> WikidataResolutionOutcome:
        if error.error_code is None:
            raise ValueError("A provider error requires an error code.")
        return self._emit(
            candidate_set=CandidateSet(
                query=query,
                provider_id="wikidata",
                candidates=candidates,
                search_complete=False,
                verification_complete=False,
                provider_error_code=error.error_code,
                incomplete_reason="provider_search_error",
            ),
            policy=policy,
            failure=error,
        )

    def _emit(
        self,
        *,
        candidate_set: CandidateSet,
        policy: ResolutionPolicy,
        failure: OperationResult | None = None,
    ) -> WikidataResolutionOutcome:
        report = self._engine.resolve(
            candidate_set=candidate_set,
            policy=policy,
            strategy=self._strategy,
        )
        selection = report.decision.selected
        if selection is not None:
            verified = next(
                c for c in candidate_set.candidates if c.reference == selection
            )
            return WikidataResolutionOutcome(
                operation=OperationResult(
                    status="success", value=selection, evidence=verified.evidence
                ),
                report=report,
            )
        if failure is not None:
            return WikidataResolutionOutcome(operation=failure, report=report)
        error_code = {
            "needs_review": "AMBIGUOUS",
            "unresolved": "NOT_FOUND",
            "incomplete_verification": "INCOMPLETE_VERIFICATION",
        }[report.decision.status]
        return WikidataResolutionOutcome(
            operation=OperationResult(
                status="failure",
                error_code=error_code,
                message=report.decision.explanation,
            ),
            report=report,
        )


def _match_kind(candidate: ResolutionCandidate) -> str | None:
    for attribute in candidate.attributes:
        if attribute.name == "match_kind" and isinstance(attribute.value, str):
            return attribute.value
    return None
