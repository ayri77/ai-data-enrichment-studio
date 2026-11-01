"""Open-Meteo geocoding provider."""

from collections.abc import Mapping
from json import JSONDecodeError
from typing import Protocol

import pycountry
from pydantic import ValidationError

from ai_data_enrichment.execution.models import (
    Evidence,
    GeoPoint,
    OperationResult,
    RuntimeValue,
)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


class JsonTransport(Protocol):
    """Fetch JSON without coupling the provider to an HTTP library."""

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object: ...


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
            expected_country_code = pycountry.countries.lookup(country).alpha_2
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
                    "count": "10",
                    "language": "en",
                    "format": "json",
                },
                timeout_seconds=10.0,
            )
        except OSError:
            return OperationResult(
                status="failure",
                error_code="PROVIDER_ERROR",
                message="Geocoding API request failed.",
            )
        except JSONDecodeError:
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Geocoding API returned malformed JSON.",
            )

        if not isinstance(payload, dict):
            return OperationResult(
                status="failure",
                error_code="INVALID_RESPONSE",
                message="Geocoding API returned a non-object response.",
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

        normalized_city = city.strip().casefold()

        matches = [
            candidate
            for candidate in results
            if isinstance(candidate, dict)
            and candidate.get("country_code") == expected_country_code
            and isinstance(candidate.get("name"), str)
            and candidate["name"].strip().casefold() == normalized_city
        ]

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
