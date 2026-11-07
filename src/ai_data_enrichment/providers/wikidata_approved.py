"""Resolve a previously user-approved Wikidata mountain without text search.

The caller MUST first validate the approval manifest against its source job
and source workbook. This adapter performs fresh type/country verification;
it never treats approval alone as a verified fact.
"""

from collections.abc import Mapping

import pycountry

from ai_data_enrichment.domain.enrichment_plan import PlanStep
from ai_data_enrichment.execution.models import EntityRef, RuntimeValue
from ai_data_enrichment.providers.http_transport import JsonTransport
from ai_data_enrichment.providers.wikidata_resolution import WikidataResolutionOutcome
from ai_data_enrichment.providers.wikidata_verification import verify_wikidata_mountain_candidate
from ai_data_enrichment.resolution.approvals import ResolutionApproval
from ai_data_enrichment.resolution.models import (
    CandidateSet,
    ResolutionAttribute,
    ResolutionCandidate,
    ResolutionDecision,
    ResolutionPolicy,
    ResolutionReport,
)

_PROVIDER_ERRORS = frozenset(
    {
        "PROVIDER_ERROR",
        "INVALID_RESPONSE",
        "RETRY_EXHAUSTED",
        "RATE_BUDGET_EXCEEDED",
        "REQUEST_BUDGET_EXCEEDED",
    }
)


def _normalized_name(text: str) -> str:
    return " ".join(text.split()).casefold()


class ApprovedWikidataMountainResolutionService:
    """A *single* approved candidate, bound to the earlier report query.

    A job-level or row-level runner must not share this service with other
    records; routing approvals to individual record/step keys is the next layer.
    """

    def __init__(
        self,
        transport: JsonTransport,
        *,
        approval: ResolutionApproval,
        source_report: ResolutionReport,
    ) -> None:
        query = source_report.candidate_set.query
        if approval.source != "wikidata" or source_report.candidate_set.provider_id != "wikidata":
            raise ValueError("Approved entity and source report must both use Wikidata.")
        if query.entity_type != "mountain":
            raise ValueError("Approved entity must come from a mountain resolution query.")
        if source_report.decision.status not in {"needs_review", "incomplete_verification"}:
            raise ValueError("Source resolution does not require user review.")

        matches = [
            candidate
            for candidate in source_report.candidate_set.candidates
            if candidate.reference == EntityRef(source="wikidata", entity_id=approval.entity_id)
        ]
        if len(matches) != 1 or matches[0].verification != "verified":
            raise ValueError("Approved entity was not verified in the source report.")
        if matches[0].entity_type != "mountain":
            raise ValueError("Approved entity does not have the required mountain type.")

        country_codes = [
            attribute.value for attribute in query.context if attribute.name == "country_code"
        ]
        if len(country_codes) != 1 or not isinstance(country_codes[0], str):
            raise ValueError("Source report lacks exactly one country_code constraint.")
        country_code = country_codes[0]
        if len(country_code) != 2 or not country_code.isupper() or not country_code.isalpha():
            raise ValueError("Source report has an invalid country_code constraint.")

        self._transport = transport
        self._approval = approval
        self._query = query
        self._country_code = country_code
        self._source_candidate = matches[0]

    def validate_step(
        self,
        step: PlanStep,
        *,
        provider_id: str,
    ) -> None:
        """Validate that this service supports the selected plan step."""

        if (
            step.capability != "resolve_entity"
            or provider_id != "wikidata"
            or step.parameters != {"entity_type": "mountain"}
        ):
            raise ValueError("Approval requires a Wikidata mountain resolution step.")

    def validate_inputs(self, inputs: Mapping[str, RuntimeValue]) -> None:
        """Reject changed row/plan constraints before any provider request."""
        if set(inputs) != {"name", "country"}:
            raise ValueError("Approved mountain resolution requires name and country only.")
        name = inputs["name"]
        country = inputs["country"]
        if not isinstance(name, str) or not isinstance(country, str):
            raise ValueError("Approved mountain name and country must be text.")
        if _normalized_name(name) != _normalized_name(self._query.name):
            raise ValueError("Current mountain name differs from approved source query.")
        try:
            country_code = pycountry.countries.lookup(country.strip()).alpha_2
        except LookupError as exc:
            raise ValueError("Current mountain country is not recognized.") from exc
        if country_code != self._country_code:
            raise ValueError("Current mountain country differs from approved source query.")

    def resolve(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        policy: ResolutionPolicy,
    ) -> WikidataResolutionOutcome:
        """Verify only the approved QID against *current* task constraints."""
        self.validate_inputs(inputs)
        country_code = self._country_code
        ref = self._source_candidate.reference
        checked = verify_wikidata_mountain_candidate(
            self._transport,
            qid=ref.entity_id,
            expected_country_code=country_code,
        )
        success = checked.status == "success"
        if success and checked.value != ref:
            raise ValueError("Fresh source identity differs from the approved entity.")
        rejected_type = checked.error_code in {
            "DIRECT_NON_MOUNTAIN_CLASS",
            "NON_MOUNTAIN_CLASS",
        }

        candidate = ResolutionCandidate(
            reference=ref,
            label=self._source_candidate.label,
            entity_type=("mountain" if success else "non_mountain" if rejected_type else "unknown"),
            verification=("verified" if success else "rejected" if rejected_type else "unchecked"),
            attributes=(
                ResolutionAttribute(
                    name="country_code",
                    value=country_code,
                    evidence=checked.evidence,
                ),
            )
            if success
            else (),
            evidence=checked.evidence if success else (),
            reason_codes=(
                "user_approved_freshly_verified"
                if success
                else "approved_non_mountain_type"
                if rejected_type
                else "user_approved_reverification_failed",
            ),
        )
        provider_error = (
            checked.error_code if not success and checked.error_code in _PROVIDER_ERRORS else None
        )
        candidate_set = CandidateSet(
            query=self._query,
            provider_id="wikidata",
            candidates=(candidate,),
            # Original candidate discovery was deliberately not repeated.
            search_complete=False,
            verification_complete=success or rejected_type,
            incomplete_reason="search_skipped_after_explicit_user_approval",
            provider_error_code=provider_error,
        )
        decision = (
            ResolutionDecision(
                status="resolved",
                selected=ref,
                basis="user_override",
                reason_codes=("user_approved", "fresh_source_verified"),
                explanation=(
                    "User selected this verified source entity in the previous job; "
                    "its mountain type and country were verified again."
                ),
            )
            if success
            else ResolutionDecision(
                status="provider_failure" if provider_error else "incomplete_verification",
                reason_codes=("approved_source_reverification_failed",),
                explanation=(
                    "The approved source entity could not be verified against the "
                    "current mountain type and country requirements."
                ),
            )
        )
        report = ResolutionReport(
            candidate_set=candidate_set,
            policy=policy,
            decision=decision,
        )
        return WikidataResolutionOutcome(operation=checked, report=report)
