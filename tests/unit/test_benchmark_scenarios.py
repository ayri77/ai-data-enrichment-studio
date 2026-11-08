"""Tests for declarative benchmark scenario validation."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_data_enrichment.evaluation.benchmark_scenarios import (
    BenchmarkScenario,
    load_scenario,
)


def _valid_config() -> dict[str, object]:
    """Return an independent valid scenario configuration."""
    return {
        "schema_version": "1.0",
        "id": "capitals_repeated",
        "description": "Repeated Berlin-Kyiv capital pairs",
        "rows": 1000,
        "dataset": "capitals",
        "unique_locations": 2,
        "distribution": "alternating",
        "resolution_strategy": "strict",
        "required_role": "country_capital",
        "memo_capacity": 1000,
        "fixture": "capitals.json",
    }


def test_load_capitals_scenario_from_repository() -> None:
    """The committed scenario must satisfy its declared contract."""
    root = Path(__file__).resolve().parents[2]
    path = root / "benchmarks/scenarios/capitals_repeated.json"

    scenario = load_scenario(path)

    assert scenario.id == "capitals_repeated"
    assert scenario.rows == 1000
    assert scenario.dataset == "capitals"
    assert scenario.unique_locations == 2
    assert scenario.required_role == "country_capital"
    assert scenario.memo_capacity == 1000


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "2.0"),
        ("rows", 0),
        ("rows", 1001),
        ("rows", True),
        ("memo_capacity", -1),
        ("memo_capacity", 3001),
        ("unique_locations", 3),
        ("distribution", "round_robin"),
        ("required_role", None),
        ("fixture", "../secrets.json"),
        ("fixture", "https://example.com/data.json"),
    ],
)
def test_invalid_capitals_scenario_is_rejected(
    field: str,
    value: object,
) -> None:
    config = _valid_config()
    config[field] = value

    with pytest.raises(ValidationError):
        BenchmarkScenario.model_validate(config)


@pytest.mark.parametrize(
    "unexpected_field",
    ["provider_url", "python_module", "allow_network"],
)
def test_unknown_fields_are_rejected(
    unexpected_field: str,
) -> None:
    config = _valid_config()
    config[unexpected_field] = "unexpected"

    with pytest.raises(ValidationError):
        BenchmarkScenario.model_validate(config)


def test_unique_cities_configuration_is_accepted() -> None:
    """Ordinary geocoding is valid without a capital constraint."""
    config = _valid_config()
    config.update(
        {
            "id": "unique_cities",
            "dataset": "unique_cities",
            "unique_locations": 250,
            "distribution": "round_robin",
            "required_role": None,
            "fixture": "cities.json",
        }
    )

    scenario = BenchmarkScenario.model_validate(config)

    assert scenario.unique_locations == 250
    assert scenario.required_role is None


def test_load_scenario_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_scenario(path)


def test_scenario_round_trip(tmp_path: Path) -> None:
    """Serialization must preserve all scenario parameters."""
    original = BenchmarkScenario.model_validate(_valid_config())
    path = tmp_path / "scenario.json"

    path.write_text(
        json.dumps(original.model_dump(mode="json")),
        encoding="utf-8",
    )

    restored = load_scenario(path)

    assert restored == original
