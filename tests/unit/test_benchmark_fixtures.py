"""Tests for deterministic offline geocoding fixtures."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ai_data_enrichment.evaluation.benchmark_fixtures import (
    GeocodingFixture,
    load_geocoding_fixture,
)


def _fixture_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "benchmarks/fixtures/capitals.json"


def _valid_fixture() -> dict[str, Any]:
    """Return an independent, mutable fixture configuration."""
    return copy.deepcopy(json.loads(_fixture_path().read_text(encoding="utf-8")))


def test_load_capitals_fixture() -> None:
    fixture = load_geocoding_fixture(_fixture_path())

    assert fixture.provider == "open-meteo"
    assert len(fixture.responses) == 2


def test_capital_responses_preserve_provider_structure() -> None:
    fixture = load_geocoding_fixture(_fixture_path())

    berlin = next(
        response.payload
        for response in fixture.responses
        if response.query.name == "Berlin" and response.query.countryCode == "DE"
    )

    kyiv = next(
        response.payload
        for response in fixture.responses
        if response.query.name == "Kyiv" and response.query.countryCode == "UA"
    )

    assert berlin.results[0].id == 2950159
    assert berlin.results[0].feature_code == "PPLC"

    assert kyiv.results[0].id == 703448
    assert kyiv.results[0].feature_code == "PPLC"


def test_missing_query_is_rejected() -> None:
    fixture = load_geocoding_fixture(_fixture_path())

    with pytest.raises(ValueError, match="No offline geocoding fixture"):
        fixture.get_response("Paris", "FR")


def test_duplicate_queries_are_rejected() -> None:
    data = _valid_fixture()
    data["responses"].append(copy.deepcopy(data["responses"][0]))

    with pytest.raises(ValidationError, match="Duplicate"):
        GeocodingFixture.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latitude", 91),
        ("latitude", -91),
        ("longitude", 181),
        ("longitude", -181),
        ("id", 0),
        ("country_code", "GER"),
        ("feature_code", ""),
    ],
)
def test_invalid_candidates_are_rejected(
    field: str,
    value: object,
) -> None:
    data = _valid_fixture()
    data["responses"][0]["payload"]["results"][0][field] = value

    with pytest.raises(ValidationError):
        GeocodingFixture.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("countryCode", "Germany"),
    ],
)
def test_invalid_queries_are_rejected(
    field: str,
    value: object,
) -> None:
    data = _valid_fixture()
    data["responses"][0]["query"][field] = value

    with pytest.raises(ValidationError):
        GeocodingFixture.model_validate(data)


def test_fixture_round_trip(tmp_path: Path) -> None:
    original = load_geocoding_fixture(_fixture_path())
    path = tmp_path / "fixture.json"

    path.write_text(
        original.model_dump_json(indent=2),
        encoding="utf-8",
    )

    restored = load_geocoding_fixture(path)

    assert restored == original
