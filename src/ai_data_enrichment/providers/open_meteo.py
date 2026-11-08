"""Open-Meteo geocoding provider."""

from collections.abc import Mapping
from json import JSONDecodeError

from pydantic import ValidationError

from ai_data_enrichment.execution.models import (
    Evidence,
    GeoPoint,
    OperationResult,
    RuntimeValue,
)
from ai_data_enrichment.providers.http_results import transport_failure_result
from ai_data_enrichment.providers.http_transport import JsonTransport, JsonTransportError
from ai_data_enrichment.providers.open_meteo_candidates import (
    administrative_area_matches,
    exact_open_meteo_matches,
)
from ai_data_enrichment.resolution.geography import normalize_country_code

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODING_RESULT_LIMIT = 100


class OpenMeteoGeocodingHandler:
    """Resolve locations using the Open-Meteo geocoding API."""

    def __init__(self, transport: JsonTransport) -> None:
        self._transport = transport

    def execute(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, str],
    ) -> OperationResult:
        """Resolve one city using its country context."""

        city = inputs.get("city")
        country = inputs.get("country")
        raw_area = inputs.get("administrative_area")
        administrative_area: str | None = None

        if "administrative_area" in inputs:
            if not isinstance(raw_area, str) or not raw_area.strip():
                return OperationResult(
                    status="failure",
                    error_code="INVALID_INPUT",
                    message="Administrative area must be non-empty text when provided.",
                )
            administrative_area = raw_area.strip()

        if (
            not isinstance(city, str)
            or not city.strip()
            or not isinstance(country, str)
            or not country.strip()
        ):
            return OperationResult(
                status="failure",
                error_code="INVALID_INPUT",
                message="City and country must be non-empty strings.",
            )

        city = city.strip()
        country = country.strip()

        # Validate the country code.
        try:
            expected_country_code = normalize_country_code(country)
        except LookupError:
            return OperationResult(
                status="failure",
                error_code="INVALID_INPUT",
                message=f"Unrecognized country: '{country}'.",
            )

        try:
            payload = self._transport.get_json(
                url=GEOCODING_URL,
                params={
                    "name": city,
                    "countryCode": expected_country_code,
                    "count": str(GEOCODING_RESULT_LIMIT),
                    "language": "en",
                    "format": "json",
                },
                timeout_seconds=10.0,
            )
        except (JsonTransportError, JSONDecodeError, OSError) as exc:
            return transport_failure_result(exc, provider_label="Geocoding API")

        if not isinstance(payload, dict):
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Geocoding API returned a non-object response.",
            )

        if "error" in payload and type(payload["error"]) is not bool:
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Geocoding API error flag must be boolean.",
            )

        if payload.get("error") is True:
            reason = payload.get("reason")

            message = (
                reason
                if isinstance(reason, str) and reason.strip()
                else "Geocoding API reported an error."
            )

            return OperationResult(
                status="failure",
                error_code="PROVIDER_ERROR",
                message=message,
            )

        # Open-Meteo may omit "results" when no locations are found.
        if "results" not in payload:
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message=f"No locations found for '{city}' in '{country}'.",
            )

        results = payload["results"]

        if not isinstance(results, list):
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Geocoding API 'results' must be a list.",
            )

        matches = exact_open_meteo_matches(
            results,
            city=city,
            country_code=expected_country_code,
        )

        if administrative_area is not None and matches:
            # A region is an explicit hard constraint, never a popularity hint.
            # Unknown region metadata cannot prove uniqueness.
            matching_region: list[dict[str, object]] = []
            missing_region_metadata = False
            for candidate in matches:
                actual_region = candidate.get("admin1")
                if not isinstance(actual_region, str) or not actual_region.strip():
                    missing_region_metadata = True
                elif administrative_area_matches(
                    administrative_area,
                    country_code=expected_country_code,
                    actual_admin1=actual_region,
                ):
                    matching_region.append(candidate)

            if missing_region_metadata:
                return OperationResult(
                    status="failure",
                    error_code="CONTEXT_UNVERIFIED",
                    message="Some matching locations lack verifiable administrative area.",
                )
            matches = matching_region

        if not matches:
            return OperationResult(
                status="failure",
                error_code="NOT_FOUND",
                message=f"No matching location for '{city}' in '{country}'.",
            )

        if len(matches) > 1:
            return OperationResult(
                status="failure",
                error_code="AMBIGUOUS",
                message=f"Multiple matching locations for '{city}' in '{country}'.",
            )

        location = matches[0]

        location_id = location.get("id")

        if type(location_id) is not int or location_id <= 0:
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Geocoding API returned an invalid location ID.",
            )

        try:
            point = GeoPoint.model_validate(
                {
                    "latitude": location.get("latitude"),
                    "longitude": location.get("longitude"),
                }
            )
        except ValidationError:
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Geocoding API returned invalid coordinates.",
            )

        return OperationResult(
            status="success",
            value=point,
            evidence=(
                Evidence(
                    source="open-meteo",
                    reference=f"geonames:{location_id}",
                ),
            ),
        )
