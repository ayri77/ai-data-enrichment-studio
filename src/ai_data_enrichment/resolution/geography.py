"""Exact geographic context comparisons shared across source adapters and ranking.

This is deterministic administrative-name/ISO normalization, not fuzzy search.
"""

from re import fullmatch

import pycountry

_AREA_PREFIXES = ("state of ", "free and hanseatic city of ")


def normalize_country_code(country: str) -> str:
    """Resolve one exact country name/code, with a single English-name alias."""
    name = country.strip()
    try:
        return pycountry.countries.lookup(name).alpha_2
    except LookupError:
        if name.casefold() == "turkey":
            return "TR"
        raise


def _area_name(value: str) -> str:
    """Normalize display prefixes and whitespace, without guessing a region."""
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
    """Match a displayed region or a country-consistent ISO 3166-2 identifier."""
    requested_name = requested.strip()
    if not requested_name:
        return False
    if fullmatch(r"[a-zA-Z]{2}-[a-zA-Z0-9]{1,3}", requested_name):
        code = requested_name.upper()
        if not code.startswith(f"{country_code}-"):
            return False
        subdivision = pycountry.subdivisions.get(code=code)
        subdivision_name = getattr(subdivision, "name", None)
        if not isinstance(subdivision_name, str) or not subdivision_name.strip():
            return False
        requested_name = subdivision_name
    return _area_name(requested_name) == _area_name(actual_admin1)
