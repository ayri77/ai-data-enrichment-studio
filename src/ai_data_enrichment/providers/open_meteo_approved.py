"""Resolve a previously user-approved Open-Meteo location for one city query.

The caller MUST first validate the approval manifest against its source job
and source workbook. This adapter repeats the same search and accepts the
approved location only if it is again a verified, eligible candidate; it never
treats approval alone as a verified fact.
"""

import re
from collections.abc import Mapping

from ai_data_enrichment.domain.enrichment_plan import PlanStep
from ai_data_enrichment.execution.models import EntityRef, GeoPoint, OperationResult, RuntimeValue
from ai_data_enrichment.providers.http_transport import JsonTransport
from ai_data_enrichment.providers.open_meteo_resolution import (
    GeocodingResolutionOutcome,
    OpenMeteoResolutionService,
)
from ai_data_enrichment.resolution.approvals import ResolutionApproval, approvable_statuses
from ai_data_enrichment.resolution.geography import normalize_country_code
from ai_data_enrichment.resolution.models import (
    ResolutionDecision,
    ResolutionPolicy,
    ResolutionReport,
)
from ai_data_enrichment.resolution.other_name import searched_under

PROVIDER_ID = "open-meteo"


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


class ApprovedOpenMeteoResolutionService:
    """A *single* approved location, bound to the earlier report query.

    One instance serves exactly one record/step. Routing approvals to the
    individual record/step keys is the caller's responsibility.
    """

    def __init__(
        self,
        transport: JsonTransport,
        *,
        approval: ResolutionApproval,
        source_report: ResolutionReport,
    ) -> None:
        candidate_set = source_report.candidate_set
        query = candidate_set.query
        if approval.source != PROVIDER_ID or candidate_set.provider_id != PROVIDER_ID:
            raise ValueError("Approved location and source report must both use Open-Meteo.")
        if query.entity_type != "city":
            raise ValueError("Approved location must come from a city resolution query.")
        if source_report.decision.status not in approvable_statuses(source_report):
            raise ValueError("Source resolution does not require user review.")
        if re.fullmatch(r"[1-9]\d*", approval.entity_id) is None:
            raise ValueError("Approved Open-Meteo location has an invalid ID.")

        reference = EntityRef(source=PROVIDER_ID, entity_id=approval.entity_id)
        matches = [c for c in candidate_set.candidates if c.reference == reference]
        if len(matches) != 1 or matches[0].verification != "verified":
            raise ValueError("Approved location was not verified in the source report.")

        context = {attribute.name: attribute.value for attribute in query.context}
        if set(context) - {"country_code", "administrative_area", "required_role"}:
            raise ValueError("Source report has an unsupported query constraint.")
        country_code = context.get("country_code")
        if not isinstance(country_code, str) or re.fullmatch(r"[A-Z]{2}", country_code) is None:
            raise ValueError("Source report requires one ISO country constraint.")
        area = context.get("administrative_area")
        role = context.get("required_role")
        if area is not None and not isinstance(area, str):
            raise ValueError("Source report has an invalid administrative area constraint.")
        if role is not None and role != "country_capital":
            raise ValueError("Source report has an unsupported role constraint.")

        self._service = OpenMeteoResolutionService(transport)
        self._reference = reference
        self._name = query.name
        # The earlier search may have used another name the user gave for the
        # row. The repeat uses it too; the row is still checked by its own value.
        self._searched_as = query.searched_as
        self._country_code = country_code
        self._area = area
        self._parameters: dict[str, str] = (
            {"required_role": "country_capital"} if role is not None else {}
        )

    def validate_step(self, step: PlanStep, *, provider_id: str) -> None:
        """Validate that this service supports the selected plan step."""
        if (
            step.capability != "geocode"
            or provider_id != PROVIDER_ID
            or step.parameters != self._parameters
        ):
            raise ValueError("Approval requires the same Open-Meteo geocoding step.")

    def validate_inputs(self, inputs: Mapping[str, RuntimeValue]) -> None:
        """Reject changed row/plan constraints before any provider request."""
        expected = {"city", "country"} | ({"administrative_area"} if self._area else set())
        if set(inputs) != expected:
            raise ValueError("Approved city resolution inputs differ from the source query.")
        city = inputs["city"]
        country = inputs["country"]
        if not isinstance(city, str) or not isinstance(country, str):
            raise ValueError("Approved city and country must be text.")
        if _normalized(city) != _normalized(self._name):
            raise ValueError("Current city differs from the approved source query.")
        try:
            country_code = normalize_country_code(country)
        except LookupError as exc:
            raise ValueError("Current country is not recognized.") from exc
        if country_code != self._country_code:
            raise ValueError("Current country differs from the approved source query.")
        if self._area is not None:
            area = inputs["administrative_area"]
            if not isinstance(area, str) or _normalized(area) != _normalized(self._area):
                raise ValueError("Current administrative area differs from the source query.")

    def resolve(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        policy: ResolutionPolicy,
        parameters: Mapping[str, str] | None = None,
    ) -> GeocodingResolutionOutcome:
        """Repeat the search and accept only the approved, still-eligible location."""
        self.validate_inputs(inputs)
        if dict(parameters or {}) != self._parameters:
            raise ValueError("Current geocoding parameters differ from the approved source query.")

        lookup = inputs if self._searched_as is None else {**inputs, "city": self._searched_as}
        fresh = self._service.resolve(inputs=lookup, policy=policy, parameters=self._parameters)
        report = fresh.report
        if self._searched_as is not None:
            report = searched_under(report, row_name=self._name, searched_as=self._searched_as)
        if report.candidate_set.provider_error_code is not None:
            # A provider failure is reported as such; approval cannot replace evidence.
            return GeocodingResolutionOutcome(operation=fresh.operation, report=report)

        candidate = next(
            (c for c in report.candidate_set.candidates if c.reference == self._reference), None
        )
        assessment = next((a for a in report.assessments if a.reference == self._reference), None)
        if (
            candidate is None
            or candidate.verification != "verified"
            or assessment is None
            or assessment.eligibility != "eligible"
        ):
            # Keep a truthful status for the fresh candidate set: anything still
            # listed can be reviewed again, otherwise the fresh outcome stands.
            status = "needs_review" if report.candidate_set.candidates else report.decision.status
            return GeocodingResolutionOutcome(
                operation=OperationResult(
                    status="failure",
                    error_code="APPROVAL_NOT_REVERIFIED",
                    message="The approved location could not be verified again.",
                ),
                report=ResolutionReport(
                    candidate_set=report.candidate_set,
                    policy=policy,
                    decision=ResolutionDecision(
                        status=status,
                        reason_codes=("approved_source_reverification_failed",),
                        explanation=(
                            "The approved location is no longer a verified, eligible "
                            "candidate for this city and country."
                        ),
                    ),
                    assessments=report.assessments,
                ),
            )

        facts = {attribute.name: attribute.value for attribute in candidate.attributes}
        point = GeoPoint.model_validate(
            {"latitude": facts["latitude"], "longitude": facts["longitude"]}
        )
        return GeocodingResolutionOutcome(
            operation=OperationResult(status="success", value=point, evidence=candidate.evidence),
            report=ResolutionReport(
                candidate_set=report.candidate_set,
                policy=policy,
                decision=ResolutionDecision(
                    status="resolved",
                    selected=self._reference,
                    basis="user_override",
                    reason_codes=("user_approved", "fresh_source_verified"),
                    explanation=(
                        "User selected this verified location in the previous job; the "
                        "search was repeated and it is again a verified, eligible candidate."
                    ),
                ),
                assessments=report.assessments,
            ),
        )
