"""Opt-in bounded Wikidata mountain resolution with complete decision provenance.

The strict CapabilityHandler remains compatible. This separate service returns
OperationResult and ResolutionReport as an inseparable pair until #65 integrates
audited resolution with job memoization and per-row review references.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from ai_data_enrichment.execution.models import Evidence, OperationResult, RuntimeValue
from ai_data_enrichment.providers.http_transport import JsonTransport
from ai_data_enrichment.providers.wikidata_candidates import normalize_wikidata_search_candidates
from ai_data_enrichment.providers.wikidata_constants import (
    WIKIDATA_ENTITY_BATCH_SIZE,
    WIKIDATA_MAX_VERIFY_CANDIDATES,
)
from ai_data_enrichment.providers.wikidata_country import (
    fetch_country_codes,
    parse_country_codes,
    referenced_countries,
)
from ai_data_enrichment.providers.wikidata_search import fetch_wikidata_search
from ai_data_enrichment.providers.wikidata_verification import (
    decided_without_requests,
    fetch_wikidata_candidate_entities,
    needs_provider_verification,
    verify_wikidata_mountain_candidate,
)
from ai_data_enrichment.resolution.engine import ResolutionEngine
from ai_data_enrichment.resolution.input_errors import check_name_length
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
    "NON_MOUNTAIN_CLASS": "no_mountain_class_path",
    "SOURCE_ENTITY_MISSING": "missing_source_entity",
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
            raise ValueError(
                "Mountain candidate verification budget is outside application limits."
            )
        self._transport = transport
        self._max_verify_candidates = max_verify_candidates
        self._engine = ResolutionEngine()
        self._strategy = MountainRankingStrategy()
        # ISO codes of the country entities seen so far. The service lives for
        # one run, so each country is asked about once per run.
        self._country_codes: dict[str, tuple[str, ...]] = {}

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
        check_name_length(name)
        country_codes = parse_country_codes(country)
        country_code = country_codes[0]

        query = ResolutionQuery(
            name=name.strip(),
            entity_type="mountain",
            context=(
                ResolutionAttribute(
                    name="country_code" if len(country_codes) == 1 else "country_codes",
                    value=country_code if len(country_codes) == 1 else "/".join(country_codes),
                ),
            ),
        )
        # Search first using the ORIGINAL input. Verification and ranking
        # are subsequent stages; neither may silently repair the query.
        search = fetch_wikidata_search(self._transport, name=name)
        try:
            normalized = normalize_wikidata_search_candidates(
                search.items,
                name=name.strip(),
                country_code=country_code,
                search_complete=search.complete,
            )
            normalized = normalized.model_copy(update={"query": query})
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
        entities = fetch_wikidata_candidate_entities(
            self._transport,
            qids=tuple(candidate.reference.entity_id for candidate in normalized.candidates),
        )
        if isinstance(entities, OperationResult):
            return self._emit(
                candidate_set=CandidateSet(
                    query=query,
                    provider_id="wikidata",
                    candidates=normalized.candidates,
                    search_complete=True,
                    verification_complete=False,
                    provider_error_code=entities.error_code,
                    incomplete_reason="provider_verification_error",
                ),
                policy=policy,
                failure=entities,
            )

        # Verify exact-label candidates before weaker search matches, and within
        # each group in the order the source returned them. Search rank is not
        # evidence that a candidate is a verified mountain in the requested
        # country, and it decides nothing here; it only says which candidates
        # the fixed budget is spent on first. The earlier order, by entity ID as
        # text, was arbitrary: for "Mont Blanc" (France/Italy) it put the summit
        # of the Alps, the first search result, fifteenth among the candidates
        # that need lookups, behind a budget of twelve, so a namesake on a
        # sub-Antarctic island was the only mountain ever examined.
        ordered = sorted(normalized.candidates, key=lambda c: _match_kind(c) != "label")

        # The budget bounds provider work, so it counts only candidates that may
        # need a request. A candidate without a class or a country is decided
        # from the claims already fetched. So is a namesake whose countries are
        # all known and are not the requested ones, and a candidate in a
        # requested country that is typed as a mountain directly. On live data
        # most candidates are of these kinds (for twelve test names, 88 of 138
        # candidates with a class and a country were mountains, mostly in other
        # countries), and spending the budget on them left real candidates
        # unexamined.
        self._learn_countries(entities)
        lookups = 0
        exceeded = False
        for candidate in ordered:
            qid = candidate.reference.entity_id
            if not decided_without_requests(
                entities.get(qid),
                accepted_country_codes=country_codes,
                known_country_codes=self._country_codes,
            ):
                if lookups >= self._max_verify_candidates:
                    exceeded = True
                    continue
                lookups += 1
            checked = verify_wikidata_mountain_candidate(
                self._transport,
                qid=qid,
                expected_country_code=country_code,
                accepted_country_codes=country_codes,
                entities=entities,
                known_country_codes=self._country_codes,
            )
            if checked.status == "success":
                replacements[qid] = candidate.model_copy(
                    update={
                        "entity_type": "mountain",
                        "verification": "verified",
                        "attributes": (
                            *candidate.attributes,
                            ResolutionAttribute(
                                name="country_code" if len(country_codes) == 1 else "country_codes",
                                value=country_code
                                if len(country_codes) == 1
                                else "/".join(country_codes),
                                evidence=checked.evidence,
                            ),
                        ),
                        "evidence": checked.evidence,
                        "reason_codes": ("source_mountain_type_and_country_verified",),
                    }
                )
            elif checked.error_code in _EXCLUDED_REASON_CODES:
                excluded = _EXCLUDED_REASON_CODES[checked.error_code]
                replacements[qid] = candidate.model_copy(
                    update={
                        "entity_type": "non_mountain"
                        if excluded != "missing_source_entity"
                        else "unknown",
                        "verification": "rejected",
                        "reason_codes": (excluded,),
                        "evidence": (Evidence(source="wikidata", reference=f"{qid}:P31"),)
                        if excluded != "missing_source_entity"
                        else (),
                    }
                )
            elif checked.error_code in {"NOT_FOUND", "UNVERIFIED_TYPE"}:
                replacements[qid] = candidate.model_copy(
                    update={"reason_codes": ("country_or_type_unverified",)}
                )
            # An exhausted ontology/provider verification is UNKNOWN, not
            # proof that the entity is absent or an eligible match.
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
        # The decision layer must be told when unverified candidates remain;
        # otherwise it could infer uniqueness from a partially searched set.
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

        return self._emit(
            candidate_set=CandidateSet(
                query=query,
                provider_id="wikidata",
                candidates=candidates,
                search_complete=True,
                verification_complete=complete,
                candidate_scan_complete=not exceeded,
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

    def _learn_countries(self, entities: Mapping[str, object]) -> None:
        """Read, in one request, the codes of the countries the candidates refer to.

        Best effort: if the request fails nothing is remembered, and the first
        candidate that needs a country asks again and reports the failure in
        the usual way.
        """
        unknown: list[str] = []
        for entity_data in entities.values():
            if not needs_provider_verification(entity_data):
                continue
            assert isinstance(entity_data, dict)
            countries = referenced_countries(entity_data["claims"])
            if isinstance(countries, OperationResult):
                continue
            unknown.extend(
                country
                for country in countries
                if country not in self._country_codes and country not in unknown
            )
        for start in range(0, len(unknown), WIKIDATA_ENTITY_BATCH_SIZE):
            fetched = fetch_country_codes(
                self._transport, unknown[start : start + WIKIDATA_ENTITY_BATCH_SIZE]
            )
            if isinstance(fetched, OperationResult):
                return
            self._country_codes.update(fetched)

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
            verified = next(c for c in candidate_set.candidates if c.reference == selection)
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
