"""Offline country normalization shared by ordinary and audited geocoding."""

import pytest
from tests.support.http import StubJsonTransport

from ai_data_enrichment.execution.models import GeoPoint
from ai_data_enrichment.providers.open_meteo import OpenMeteoGeocodingHandler
from ai_data_enrichment.providers.open_meteo_resolution import OpenMeteoResolutionService
from ai_data_enrichment.providers.wikidata_country import parse_country_codes
from ai_data_enrichment.resolution.geography import normalize_country_code
from ai_data_enrichment.resolution.input_errors import UnusableInput
from ai_data_enrichment.resolution.models import ResolutionPolicy


@pytest.mark.parametrize(
    ("country", "expected_code", "city"),
    [
        ("Turkey", "TR", "Ankara"),
        ("Türkiye", "TR", "Ankara"),
        ("TR", "TR", "Ankara"),
        ("  tUrKeY \t", "TR", "Ankara"),
        (" tr ", "TR", "Ankara"),
        ("Germany", "DE", "Berlin"),
        (" DEU ", "DE", "Berlin"),
        (" Ukraine ", "UA", "Kyiv"),
    ],
)
@pytest.mark.parametrize("audited", [False, True])
@pytest.mark.parametrize("matching_locations", [1, 2])
def test_country_normalization_reaches_both_geocoding_paths_without_changing_ambiguity(
    country: str, expected_code: str, city: str, audited: bool, matching_locations: int
) -> None:
    assert normalize_country_code(country) == expected_code
    # Fictional locations isolate country matching and ambiguity from live data.
    transport = StubJsonTransport(
        {
            "results": [
                {
                    "id": 1000 + index,
                    "name": city,
                    "country_code": expected_code,
                    "feature_code": "PPLC",
                    "latitude": 40.0,
                    "longitude": 30.0,
                }
                for index in range(matching_locations)
            ]
        }
    )
    inputs = {"city": city, "country": country}
    if audited:
        outcome = OpenMeteoResolutionService(transport).resolve(
            inputs=inputs, policy=ResolutionPolicy(policy_id="strict", version="1")
        )
        result = outcome.operation
        assert {a.name: a.value for a in outcome.report.candidate_set.query.context} == {
            "country_code": expected_code
        }
        assert len(outcome.report.candidate_set.candidates) == matching_locations
    else:
        result = OpenMeteoGeocodingHandler(transport).execute(inputs=inputs, parameters={})
    assert len(transport.requests) == 1
    assert transport.requests[0][1]["countryCode"] == expected_code
    if matching_locations == 1:
        assert result.value == GeoPoint(latitude=40.0, longitude=30.0)
    else:
        assert result.error_code == "AMBIGUOUS"
        assert result.value is None


@pytest.mark.parametrize(
    ("written", "code"),
    [
        # Common English names that are not the ISO names.
        ("Russia", "RU"),
        ("UK", "GB"),
        ("USA", "US"),
        ("South Korea", "KR"),
        ("Czech Republic", "CZ"),
        ("Ivory Coast", "CI"),
        ("Holland", "NL"),
        # Other languages and scripts.
        ("Росія", "RU"),
        ("Россия", "RU"),
        ("Russland", "RU"),
        ("Німеччина", "DE"),
        ("Deutschland", "DE"),
        ("Estados Unidos", "US"),
        ("日本", "JP"),
        # Case, spacing, width and the shape of the apostrophe do not matter.
        ("  rUsSlAnD ", "RU"),
        ("ＪＡＰＡＮ", "JP"),
        ("Côte d'Ivoire", "CI"),
        ("Côte d\u2019Ivoire", "CI"),
    ],
)
def test_a_country_is_recognized_as_people_write_it(written: str, code: str) -> None:
    assert normalize_country_code(written) == code


@pytest.mark.parametrize(
    "written",
    [
        "Atlantis",
        "Narnia",
        "Itly",  # a misspelling is not corrected
        "Italy, France",  # a list is not one country
        "Kongo",  # two countries in different languages: neither is chosen
        "Kosovo",  # no ISO 3166-1 code
        "США",  # short forms beyond the main CLDR name are not known
        "Great Britain",
    ],
)
def test_nothing_is_guessed_for_a_name_that_is_not_exactly_one_country(written: str) -> None:
    with pytest.raises(LookupError):
        normalize_country_code(written)


@pytest.mark.parametrize("country", ["Unknown country", "Turke", "Turkey/China", "Nepal/China"])
def test_unknown_or_multi_country_geocoding_input_remains_invalid(country: str) -> None:
    with pytest.raises(LookupError):
        normalize_country_code(country)
    transport = StubJsonTransport({"results": []})
    result = OpenMeteoGeocodingHandler(transport).execute(
        inputs={"city": "Example", "country": country}, parameters={}
    )
    assert result.error_code == "INVALID_INPUT"
    assert result.message == f"Unrecognized country: '{country}'."
    # The audited service names it a problem of this cell, so a run can go on.
    with pytest.raises(UnusableInput) as rejection:
        OpenMeteoResolutionService(transport).resolve(
            inputs={"city": "Example", "country": country},
            policy=ResolutionPolicy(policy_id="strict", version="1"),
        )
    assert rejection.value.error_code == "UNRECOGNIZED_COUNTRY"
    assert not transport.requests


@pytest.mark.parametrize(
    ("country", "expected_codes"),
    [
        ("Nepal/China", ("CN", "NP")),
        ("Pakistan/China", ("CN", "PK")),
        (" China / Nepal / NP ", ("CN", "NP")),
        ("Turkey", ("TR",)),
    ],
)
def test_mountain_parser_keeps_multi_country_semantics(
    country: str, expected_codes: tuple[str, ...]
) -> None:
    assert parse_country_codes(country) == expected_codes


@pytest.mark.parametrize("country", ["", "Nepal/", "/China", "Nepal//China", "Unknown country"])
def test_mountain_parser_keeps_invalid_country_errors(country: str) -> None:
    with pytest.raises(UnusableInput) as rejection:
        parse_country_codes(country)
    assert rejection.value.error_code == "UNRECOGNIZED_COUNTRY"
    # Direct callers that expect a ValueError for bad input keep working.
    assert isinstance(rejection.value, ValueError)
