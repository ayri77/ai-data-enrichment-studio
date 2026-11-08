"""Acceptance tests for the offline M3 Excel benchmark."""

import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from ai_data_enrichment.evaluation.benchmark_fixtures import (
    load_geocoding_fixture,
)
from ai_data_enrichment.evaluation.benchmark_scenarios import load_scenario
from ai_data_enrichment.evaluation.m3_excel_benchmark import (
    _create_source,
    _expected_distances,
    _locations_from_fixture,
    run_benchmark,
    run_configured_benchmark,
)


def test_m3_excel_benchmark_eight_rows() -> None:
    """Verify deterministic Excel, memoization and audit invariants."""
    result = run_benchmark(rows=8)

    assert result.rows == 8
    assert result.successful_rows == 8
    assert result.failed_rows == 0

    assert result.logical_operations == 24
    assert result.executed_handler_calls == 4
    assert result.reused_operations == 20
    assert result.transport_calls == 2

    assert result.audit_operations == 2
    assert result.audit_record_step_links == 16

    assert result.output_rows_verified == 8
    assert result.source_unchanged is True

    # Timing is informative, not a fixed CI performance threshold.
    assert result.elapsed_seconds > 0
    assert result.rows_per_second > 0


@pytest.mark.parametrize("rows", [0, -1, 1001])
def test_m3_excel_benchmark_rejects_invalid_row_counts(rows: int) -> None:
    with pytest.raises(ValueError, match="1 to 1000"):
        run_benchmark(rows=rows)


def test_m3_excel_benchmark_rejects_boolean_row_count() -> None:
    with pytest.raises(ValueError, match="1 to 1000"):
        run_benchmark(rows=True)


def test_benchmark_uses_external_fixture(tmp_path: Path) -> None:
    """The benchmark must use the supplied fixture file."""
    root = Path(__file__).resolve().parents[2]
    source = root / "benchmarks/fixtures/capitals.json"

    fixture_data = json.loads(source.read_text(encoding="utf-8"))

    # Keep Berlin only: Kyiv must no longer be available.
    fixture_data["responses"] = [
        response for response in fixture_data["responses"] if response["query"]["name"] == "Berlin"
    ]

    fixture_path = tmp_path / "incomplete_capitals.json"
    fixture_path.write_text(
        json.dumps(fixture_data),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="No offline geocoding fixture",
    ):
        run_benchmark(
            rows=8,
            fixture_path=fixture_path,
        )


def test_benchmark_accepts_explicit_capitals_fixture() -> None:
    """Explicit fixture loading must preserve benchmark results."""
    root = Path(__file__).resolve().parents[2]
    fixture_path = root / "benchmarks/fixtures/capitals.json"

    result = run_benchmark(
        rows=8,
        fixture_path=fixture_path,
    )

    assert result.successful_rows == 8
    assert result.failed_rows == 0
    assert result.transport_calls == 2
    assert result.executed_handler_calls == 4
    assert result.audit_record_step_links == 16
    assert result.source_unchanged is True


def test_unique_cities_source_generation(
    tmp_path: Path,
) -> None:
    """Generate deterministic round-robin city pairs."""
    locations = (
        ("Berlin", "Germany"),
        ("Kyiv", "Ukraine"),
        ("Hamburg", "Germany"),
        ("Munich", "Germany"),
    )

    source = tmp_path / "unique_cities.xlsx"

    _create_source(
        source,
        rows=8,
        locations=locations,
    )

    workbook = load_workbook(source, read_only=True)

    try:
        sheet = workbook["Capitals"]

        actual = [
            (
                sheet.cell(row=i, column=1).value,
                sheet.cell(row=i, column=3).value,
            )
            for i in range(2, 10)
        ]

        expected = [
            ("Berlin", "Kyiv"),
            ("Kyiv", "Hamburg"),
            ("Hamburg", "Munich"),
            ("Munich", "Berlin"),
        ] * 2

        assert actual == expected
        assert workbook["Notes"]["A1"].value == ("Preserve this worksheet")
    finally:
        workbook.close()


def test_unique_cities_source_uses_fixture(
    tmp_path: Path,
) -> None:
    """Generate Excel rows from externally configured locations."""

    root = Path(__file__).resolve().parents[2]
    fixture_path = root / "benchmarks/fixtures/cities.json"

    fixture = load_geocoding_fixture(fixture_path)
    locations = _locations_from_fixture(fixture)

    assert locations == (
        ("Berlin", "DE"),
        ("Kyiv", "UA"),
        ("Hamburg", "DE"),
        ("Munich", "DE"),
    )

    source = tmp_path / "cities.xlsx"

    _create_source(
        source,
        rows=8,
        locations=locations,
    )

    workbook = load_workbook(source, read_only=True)

    try:
        sheet = workbook["Capitals"]

        expected = [
            ("Berlin", "DE", "Kyiv", "UA"),
            ("Kyiv", "UA", "Hamburg", "DE"),
            ("Hamburg", "DE", "Munich", "DE"),
            ("Munich", "DE", "Berlin", "DE"),
        ] * 2

        actual = [
            tuple(sheet.cell(row=row, column=column).value for column in range(1, 5))
            for row in range(2, 10)
        ]

        assert actual == expected
    finally:
        workbook.close()


def test_unique_cities_expected_distances() -> None:
    """Reference distances follow fixture round-robin order."""
    root = Path(__file__).resolve().parents[2]
    fixture = load_geocoding_fixture(root / "benchmarks/fixtures/cities.json")

    distances = _expected_distances(fixture, rows=8)

    assert len(distances) == 8
    assert all(distance > 0 for distance in distances)

    # The four-pair sequence repeats after four rows.
    assert distances[:4] == distances[4:]

    # Different geographic pairs must produce different distances.
    assert len({round(value, 3) for value in distances}) == 4


def test_unique_cities_e2e_benchmark() -> None:
    """Execute the complete unique-cities Excel pipeline."""
    root = Path(__file__).resolve().parents[2]
    scenario_path = root / "benchmarks/scenarios/unique_cities.json"

    scenario = load_scenario(scenario_path)
    result = run_configured_benchmark(scenario)

    assert result.rows == 8
    assert result.logical_operations == 24

    assert result.executed_handler_calls == 8
    assert result.reused_operations == 16
    assert result.transport_calls == 4

    assert result.audit_operations == 4
    assert result.audit_record_step_links == 16

    assert result.successful_rows == 8
    assert result.failed_rows == 0
    assert result.output_rows_verified == 8
    assert result.source_unchanged is True


def test_unique_cities_memoization_disabled() -> None:
    """Disabling memoization must preserve results and audit coverage."""
    root = Path(__file__).resolve().parents[2]
    fixture_path = root / "benchmarks/fixtures/cities.json"

    result = run_benchmark(
        rows=8,
        fixture_path=fixture_path,
        dataset="unique_cities",
        required_role=None,
        memo_capacity=0,
    )

    assert result.rows == 8
    assert result.logical_operations == 24

    assert result.executed_handler_calls == 24
    assert result.reused_operations == 0
    assert result.transport_calls == 16

    # The collector merges identical reports even without memoization.
    assert result.audit_operations == 4
    assert result.audit_record_step_links == 16

    assert result.successful_rows == 8
    assert result.failed_rows == 0
    assert result.output_rows_verified == 8
    assert result.source_unchanged is True
