"""What the source records about a candidate, for a person telling namesakes apart.

Fourteen verified mountains called "Mount Washington" in one country look the
same by name and country. The source's own description, coordinates and
elevation tell them apart, and all three are already in the responses that
verification fetched: nothing here makes a request.

These attributes are for reading only. No decision uses them, and the value a
plan writes still comes from its own lookup step.
"""

from ai_data_enrichment.execution.models import Evidence, OperationResult
from ai_data_enrichment.providers.wikidata_constants import WIKIDATA_P_ELEVATION
from ai_data_enrichment.providers.wikidata_parsing import (
    _best_ranked_statements,
    _parse_elevation_quantity,
)
from ai_data_enrichment.resolution.models import ResolutionAttribute

WIKIDATA_P_COORDINATES = "P625"
_EARTH = "http://www.wikidata.org/entity/Q2"
MAX_DESCRIPTION_LENGTH = 160


def description_fact(search_hit: dict[str, object]) -> tuple[ResolutionAttribute, ...]:
    """The description the search returned with the hit, shortened if long."""
    text = search_hit.get("description")
    if not isinstance(text, str) or not text.strip():
        return ()
    text = " ".join(text.split())
    if len(text) > MAX_DESCRIPTION_LENGTH:
        text = text[: MAX_DESCRIPTION_LENGTH - 1].rstrip() + "…"
    return (ResolutionAttribute(name="description", value=text),)


def _single(claims: dict[str, object], property_id: str) -> dict[str, object] | None:
    statements = claims.get(property_id)
    if not isinstance(statements, list):
        return None
    best = _best_ranked_statements(statements)
    # Several equally ranked values are not one fact to show.
    return best[0] if len(best) == 1 else None


def recorded_facts(qid: str, entity_data: object) -> tuple[ResolutionAttribute, ...]:
    """Coordinates and elevation as recorded, each only when unambiguous.

    Anything missing, repeated or malformed is left out: a fact that cannot be
    read plainly is not shown, and never an error.
    """
    claims = entity_data.get("claims") if isinstance(entity_data, dict) else None
    if not isinstance(claims, dict):
        return ()
    facts: list[ResolutionAttribute] = []

    try:
        place = _single(claims, WIKIDATA_P_COORDINATES)
        value = place["mainsnak"]["datavalue"]["value"] if place else None  # type: ignore[index]
        if (
            isinstance(value, dict)
            and value.get("globe") == _EARTH
            and type(value.get("latitude")) in (int, float)
            and type(value.get("longitude")) in (int, float)
            and -90 <= value["latitude"] <= 90
            and -180 <= value["longitude"] <= 180
        ):
            evidence = (Evidence(source="wikidata", reference=f"{qid}:{WIKIDATA_P_COORDINATES}"),)
            facts.append(
                ResolutionAttribute(
                    name="latitude", value=float(value["latitude"]), evidence=evidence
                )
            )
            facts.append(
                ResolutionAttribute(
                    name="longitude", value=float(value["longitude"]), evidence=evidence
                )
            )
    except (KeyError, TypeError, ValueError):
        pass

    try:
        height = _single(claims, WIKIDATA_P_ELEVATION)
        elevation = _parse_elevation_quantity(height) if height else None
        if elevation is not None and not isinstance(elevation, OperationResult):
            if elevation.unit == "m":
                facts.append(
                    ResolutionAttribute(
                        name="elevation_m",
                        value=elevation.value,
                        evidence=(
                            Evidence(source="wikidata", reference=f"{qid}:{WIKIDATA_P_ELEVATION}"),
                        ),
                    )
                )
    except (KeyError, TypeError, ValueError, OverflowError):
        pass

    return tuple(facts)
