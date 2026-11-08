"""Open-Meteo candidate normalization and explicit administrative context matching.

Source-specific metadata stays here; the shared Resolution Engine consumes
normalized, typed candidates and never interprets GeoNames feature codes.
This module is pure: it makes no HTTP calls and takes no ranking decisions.
"""

from collections.abc import Sequence
from re import fullmatch

from pydantic import ValidationError

from ai_data_enrichment.execution.models import EntityRef, Evidence, GeoPoint
from ai_data_enrichment.resolution.geography import (
    administrative_area_matches as administrative_area_matches,
)
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


def exact_open_meteo_matches(
    results: Sequence[object],
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


def normalize_open_meteo_candidates(
    results: Sequence[object],
    *,
    city: str,
    country_code: str,
    administrative_area: str | None = None,
    result_limit: int = 10,
    required_role: str | None = None,
) -> CandidateSet:
    """Return verified exact-match candidates, not a resolution decision.

    A saturated result page is marked incomplete: Open-Meteo's first page
    cannot prove that additional homonyms are absent.
    """
    if not city.strip() or fullmatch(r"[A-Z]{2}", country_code) is None:
        raise ValueError("City and ISO country code are required.")
    if administrative_area is not None and not administrative_area.strip():
        raise ValueError("Optional administrative area must be nonblank.")
    if result_limit < 1 or len(results) > result_limit:
        raise ValueError("Open-Meteo candidate list exceeds the configured bound.")
    if required_role is not None and required_role != "country_capital":
        raise ValueError("Unsupported required geographic role.")

    query_context = [ResolutionAttribute(name="country_code", value=country_code)]

    if administrative_area is not None:
        query_context.append(
            ResolutionAttribute(
                name="administrative_area",
                value=administrative_area.strip(),
            )
        )

    if required_role is not None:
        query_context.append(
            ResolutionAttribute(
                name="required_role",
                value=required_role,
            )
        )

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
            attributes.append(ResolutionAttribute(name="administrative_area", value=admin1.strip()))

        feature_code = match.get("feature_code")
        if feature_code is not None:
            if not isinstance(feature_code, str):
                raise ValueError("Open-Meteo feature code must be text.")
            role = _OPEN_METEO_ROLES.get(feature_code)
            if role is not None:
                attributes.append(ResolutionAttribute(name="administrative_role", value=role))

        population = match.get("population")
        if population is not None:
            if type(population) is not int or population < 0:
                raise ValueError("Open-Meteo population must be nonnegative integer.")
            attributes.append(ResolutionAttribute(name="population", value=population))

        if feature_code == "PPLX":
            entity_type = "district"
        elif feature_code in _OPEN_METEO_ROLES:
            entity_type = "city"
        elif feature_code == "PPL":
            entity_type = "settlement"
        else:
            entity_type = "location"

        candidates.append(
            ResolutionCandidate(
                reference=EntityRef(source="open-meteo", entity_id=str(raw_id)),
                label=city.strip(),
                entity_type=entity_type,
                verification="verified",
                attributes=tuple(attributes),
                evidence=(Evidence(source="open-meteo", reference=f"geonames:{raw_id}"),),
            )
        )

    complete = len(results) < result_limit
    return CandidateSet(
        provider_id="open-meteo",
        query=ResolutionQuery(
            name=city.strip(),
            entity_type="city",
            context=tuple(query_context),
        ),
        candidates=tuple(candidates),
        search_complete=complete,
        verification_complete=True,
        incomplete_reason=None if complete else "result_limit_reached",
    )
