"""Open-Meteo candidate normalization and explicit administrative context matching.

Source-specific metadata stays here; the shared Resolution Engine consumes
normalized, typed candidates and never interprets GeoNames feature codes.
This module is pure: it makes no HTTP calls and takes no ranking decisions.
"""

from collections.abc import Mapping
from re import fullmatch

import pycountry
from pydantic import ValidationError

from ai_data_enrichment.execution.models import EntityRef, Evidence, GeoPoint
from ai_data_enrichment.resolution.models import (
    CandidateSet,
    ResolutionAttribute,
    ResolutionCandidate,
    ResolutionQuery,
)

# These are provider semantics, *not* universal feature IDs.
_OPEN_METEO_ROLES = {
    "PPLC": "country_capital",
    "PPLA": "administrative_seat",
    "PPLA2": "administrative_seat",
    "PPLA3": "administrative_seat",
    "PPLA4": "administrative_seat",
}
_AREA_PREFIXES = ("state of ", "free and hanseatic city of ")


def exact_open_meteo_matches(
    results: list[object],
    *,
    city: str,
    country_code: str,
) -> list[dict[str, object]]:
    """Retain only exact name + country matches, ignoring unrelated search hits."""
    normalized_city = city.strip().casefold()
    return [
        candidate
        for candidate in results
        if isinstance(candidate, dict)
        and candidate.get("country_code") == country_code
        and isinstance(candidate.get("name"), str)
        and candidate["name"].strip().casefold() == normalized_city
    ]


def _area_name(value: str) -> str:
    """Provider-specific display-name normalization, never fuzzy geography."""
    name = " ".join(value.casefold().split())
    for prefix in _AREA_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def administrative_area_matches(
    requested: str,
    *,
    country_code: str,
    actual_admin1: str,
) -> bool:
    """Match displayed admin1 or an ISO 3166-2 code for the specified country."""
    requested_name = requested.strip()
    if not requested_name:
        return False
    if fullmatch(r"[a-zA-Z]{2}-[a-zA-Z0-9]{1,3}", requested_name):
        code = requested_name.upper()
        if not code.startswith(f"{country_code}-"):
            return False
        subdivision = pycountry.subdivisions.get(code=code)
        if subdivision is None:
            return False
        requested_name = subdivision.name
    return _area_name(requested_name) == _area_name(actual_admin1)


def normalize_open_meteo_candidates(
    results: list[object],
    *,
    city: str,
    country_code: str,
    result_limit: int = 10,
) -> CandidateSet:
    """Return verified exact-match candidates, not a resolution decision.

    A saturated result page is marked incomplete: Open-Meteo's first page
    cannot prove that additional homonyms are absent.
    """
    if not city.strip() or fullmatch(r"[A-Z]{2}", country_code) is None:
        raise ValueError("City and ISO country code are required.")
    if result_limit < 1 or len(results) > result_limit:
        raise ValueError("Open-Meteo candidate list exceeds the configured bound.")

    candidates: list[ResolutionCandidate] = []
    for match in exact_open_meteo_matches(results, city=city, country_code=country_code):
        raw_id = match.get("id")
        if type(raw_id) is not int or raw_id <= 0:
            raise ValueError("Exact-matched Open-Meteo location has an invalid ID.")
        try:
            point = GeoPoint.model_validate(
                {"latitude": match.get("latitude"), "longitude": match.get("longitude")}
            )
        except ValidationError as exc:
            raise ValueError("Exact-matched Open-Meteo coordinates are invalid.") from exc

        attributes = [
            ResolutionAttribute(name="country_code", value=country_code),
            ResolutionAttribute(name="latitude", value=point.latitude),
            ResolutionAttribute(name="longitude", value=point.longitude),
        ]

        admin1 = match.get("admin1")
        if admin1 is not None:
            if not isinstance(admin1, str) or not admin1.strip():
                raise ValueError("Open-Meteo administrative area must be nonblank text.")
            attributes.append(
                ResolutionAttribute(name="administrative_area", value=admin1.strip())
            )

        feature_code = match.get("feature_code")
        if feature_code is not None:
            if not isinstance(feature_code, str):
                raise ValueError("Open-Meteo feature code must be text.")
            role = _OPEN_METEO_ROLES.get(feature_code)
            if role is not None:
                attributes.append(
                    ResolutionAttribute(name="administrative_role", value=role)
                )

        population = match.get("population")
        if population is not None:
            if type(population) is not int or population < 0:
                raise ValueError("Open-Meteo population must be nonnegative integer.")
            attributes.append(ResolutionAttribute(name="population", value=population))

        if feature_code == "PPLX":
            entity_type = "district"
        elif feature_code in _OPEN_METEO_ROLES:
            entity_type = "city"
        else:
            entity_type = "settlement"

        candidates.append(
            ResolutionCandidate(
                reference=EntityRef(source="open-meteo", entity_id=str(raw_id)),
                label=city.strip(),
                entity_type=entity_type,
                verification="verified",
                attributes=tuple(attributes),
                evidence=(
                    Evidence(source="open-meteo", reference=f"geonames:{raw_id}"),
                ),
            )
        )

    complete = len(results) < result_limit
    return CandidateSet(
        provider_id="open-meteo",
        query=ResolutionQuery(
            name=city.strip(),
            entity_type="city",
            context=(ResolutionAttribute(name="country_code", value=country_code),),
        ),
        candidates=tuple(candidates),
        search_complete=complete,
        verification_complete=True,
        incomplete_reason=None if complete else "result_limit_reached",
    )
