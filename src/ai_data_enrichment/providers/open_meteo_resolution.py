"""Opt-in Open-Meteo resolution service with inseparable result and audit report.

Unlike the legacy CapabilityHandler.execute, this API always returns the
machine-readable ResolutionReport alongside an OperationResult. JobExecutor
integration and policy-aware persistent caching belong to issue #65.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from json import JSONDecodeError

from pydantic import ValidationError

from ai_data_enrichment.execution.models import GeoPoint, OperationResult, RuntimeValue
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError
from ai_data_enrichment.providers.open_meteo import GEOCODING_RESULT_LIMIT, GEOCODING_URL
from ai_data_enrichment.providers.open_meteo_candidates import normalize_open_meteo_candidates
from ai_data_enrichment.resolution.engine import ResolutionEngine
from ai_data_enrichment.resolution.geographic import GeographicRankingStrategy
from ai_data_enrichment.resolution.geography import normalize_country_code
from ai_data_enrichment.resolution.models import (
    CandidateSet,
    ResolutionAttribute,
    ResolutionPolicy,
    ResolutionQuery,
    ResolutionReport,
)


@dataclass(frozen=True)
class GeocodingResolutionOutcome:
    """Typed execution result paired with its complete selection/audit report."""

    operation: OperationResult
    report: ResolutionReport


class OpenMeteoResolutionService:
    """Make one bounded provider request, then apply the shared decision engine."""

    def __init__(self, transport: JsonTransport) -> None:
        self._transport = transport
        self._engine = ResolutionEngine()
        self._strategy = GeographicRankingStrategy()

    def resolve(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        policy: ResolutionPolicy,
        parameters: Mapping[str, str] | None = None,
    ) -> GeocodingResolutionOutcome:
        """Resolve a geocoding input with a caller-controlled, versioned policy.

        This is intentionally *not* a CapabilityHandler yet: silently discarding
        a report while accepting a ranked result would break audit guarantees.
        """
        params = parameters if parameters is not None else {}

        if set(params) - {"required_role"}:
            raise ValueError("Unregistered geocoding resolution parameter.")

        required_role = params.get("required_role")

        if required_role is not None and required_role != "country_capital":
            raise ValueError("Unsupported required geographic role.")

        unknown = set(inputs) - {"city", "country", "administrative_area"}
        if unknown:
            raise ValueError("Unregistered geocoding resolution input.")

        city = inputs.get("city")
        country = inputs.get("country")
        region = inputs.get("administrative_area")
        if not isinstance(city, str) or not city.strip():
            raise ValueError("Resolution requires a nonblank city.")
        if not isinstance(country, str) or not country.strip():
            raise ValueError("Resolution requires a nonblank country.")
        if "administrative_area" in inputs and (not isinstance(region, str) or not region.strip()):
            raise ValueError("Explicit administrative area must be nonblank text.")
        area = region.strip() if isinstance(region, str) else None
        try:
            country_code = normalize_country_code(country)
        except LookupError as exc:
            raise ValueError("Resolution requires a recognized country.") from exc

        context = [ResolutionAttribute(name="country_code", value=country_code)]

        if area is not None:
            context.append(ResolutionAttribute(name="administrative_area", value=area))

        if required_role is not None:
            context.append(ResolutionAttribute(name="required_role", value=required_role))

        query = ResolutionQuery(
            name=city.strip(),
            entity_type="city",
            context=tuple(context),
        )

        try:
            payload = self._transport.get_json(
                url=GEOCODING_URL,
                params={
                    "name": city.strip(),
                    "countryCode": country_code,
                    "count": str(GEOCODING_RESULT_LIMIT),
                    "language": "en",
                    "format": "json",
                },
                timeout_seconds=10.0,
            )
        except (JsonTransportError, JSONDecodeError, OSError) as exc:
            failure = transport_failure_result(exc, provider_label="Geocoding API")
            return self._failure(query=query, policy=policy, result=failure)

        if not isinstance(payload, dict):
            return self._invalid_response(query, policy)
        if "error" in payload and type(payload["error"]) is not bool:
            return self._invalid_response(query, policy)
        if payload.get("error") is True:
            return self._failure(
                query=query,
                policy=policy,
                result=OperationResult(
                    status="failure",
                    error_code="PROVIDER_ERROR",
                    message="Geocoding API reported an error.",
                ),
            )

        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            return self._invalid_response(query, policy)

        try:
            candidate_set = normalize_open_meteo_candidates(
                raw_results,
                city=city.strip(),
                country_code=country_code,
                administrative_area=area,
                result_limit=GEOCODING_RESULT_LIMIT,
                required_role=required_role,
            )
        except (ValueError, ValidationError):
            return self._invalid_response(query, policy)

        report = self._engine.resolve(
            candidate_set=candidate_set,
            policy=policy,
            strategy=self._strategy,
        )
        selection = report.decision.selected
        if selection is not None:
            candidate = next(
                candidate
                for candidate in candidate_set.candidates
                if candidate.reference == selection
            )
            attributes = {attribute.name: attribute.value for attribute in candidate.attributes}
            # The source adapter validated these coordinates before verification.
            point = GeoPoint.model_validate(
                {
                    "latitude": attributes["latitude"],
                    "longitude": attributes["longitude"],
                }
            )
            return GeocodingResolutionOutcome(
                operation=OperationResult(
                    status="success",
                    value=point,
                    evidence=candidate.evidence,
                ),
                report=report,
            )

        error_codes = {
            "needs_review": "AMBIGUOUS",
            "unresolved": "NOT_FOUND",
            "incomplete_verification": "INCOMPLETE_VERIFICATION",
        }
        return GeocodingResolutionOutcome(
            operation=OperationResult(
                status="failure",
                error_code=error_codes[report.decision.status],
                message=report.decision.explanation,
            ),
            report=report,
        )

    def _invalid_response(
        self,
        query: ResolutionQuery,
        policy: ResolutionPolicy,
    ) -> GeocodingResolutionOutcome:
        return self._failure(
            query=query,
            policy=policy,
            result=OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Geocoding API returned invalid candidate data.",
            ),
        )

    def _failure(
        self,
        *,
        query: ResolutionQuery,
        policy: ResolutionPolicy,
        result: OperationResult,
    ) -> GeocodingResolutionOutcome:
        if result.status != "failure" or result.error_code is None:
            raise ValueError("Provider failure must have a typed failure code.")
        candidate_set = CandidateSet(
            query=query,
            provider_id="open-meteo",
            search_complete=False,
            verification_complete=False,
            provider_error_code=result.error_code,
        )
        report = self._engine.resolve(
            candidate_set=candidate_set,
            policy=policy,
            strategy=self._strategy,
        )
        return GeocodingResolutionOutcome(operation=result, report=report)
