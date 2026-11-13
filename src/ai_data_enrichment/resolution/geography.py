"""Exact geographic context comparisons shared across source adapters and ranking.

This is deterministic administrative-name/ISO normalization, not fuzzy search.
"""

from functools import cache
from re import fullmatch
from unicodedata import normalize

import pycountry

_AREA_PREFIXES = ("state of ", "free and hanseatic city of ")


def _name_key(name: str) -> str:
    """Compare names without regard to case, spacing, width or apostrophe shape."""
    text = normalize("NFKC", name).replace("\u2019", "'").replace("\u02bc", "'")
    return " ".join(text.casefold().split())


@cache
def _country_names() -> dict[str, frozenset[str]]:
    """Country names in every language CLDR has, mapped to ISO codes.

    Built once per process, on the first name the English table does not know.
    A name that stands for different countries in different languages keeps all
    of them here and is then not recognized: a wrong country is worse than none.
    """
    # Imported here: a run whose countries are all plain English names or
    # codes never needs the library, and every run starts a fresh process.
    from babel import Locale, UnknownLocaleError
    from babel.localedata import locale_identifiers

    known = {country.alpha_2 for country in pycountry.countries}
    names: dict[str, set[str]] = {}
    for identifier in locale_identifiers():
        if "_" in identifier:
            # Regional variants repeat their base language.
            continue
        try:
            territories = Locale.parse(identifier).territories
        except (ValueError, UnknownLocaleError):
            continue
        for code, name in territories.items():
            if code in known and name:
                names.setdefault(_name_key(name), set()).add(code)
    return {name: frozenset(codes) for name, codes in names.items()}


def normalize_country_code(country: str) -> str:
    """Resolve one country name or code to its ISO 3166-1 alpha-2 code.

    English names and ISO codes are looked up first, as before. Anything else
    is looked up among the country names of the Unicode CLDR in all its
    languages ("Russia", "Росія", "Russland", "Deutschland", "UK"), and accepted
    only when it names exactly one country. Nothing is guessed: a misspelling,
    a list of countries or an invented name raises ``LookupError``.
    """
    name = country.strip()
    try:
        return pycountry.countries.lookup(name).alpha_2
    except LookupError:
        if name.casefold() == "turkey":
            # Also in the CLDR table; answered here so that the bundled example,
            # which has this one name, does not build the table on every run.
            return "TR"
        codes = _country_names().get(_name_key(name), frozenset())
        if len(codes) != 1:
            raise
        return next(iter(codes))


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
