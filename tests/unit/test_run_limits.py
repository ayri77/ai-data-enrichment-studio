import pytest

from ai_data_enrichment import run_limits
from ai_data_enrichment.run_limits import bounded


def test_defaults_are_the_demo_bounds(monkeypatch) -> None:
    for name in ("ROWS", "REQUESTS", "WAIT", "TIMEOUT"):
        monkeypatch.delenv(f"LIMIT_{name}", raising=False)
    assert bounded("LIMIT_ROWS", 20, 5000) == 20
    monkeypatch.setenv("LIMIT_ROWS", "  ")
    assert bounded("LIMIT_ROWS", 20, 5000) == 20


def test_an_experiment_may_raise_a_bound(monkeypatch) -> None:
    monkeypatch.setenv("LIMIT_ROWS", "500")
    assert bounded("LIMIT_ROWS", 20, 5000) == 500


@pytest.mark.parametrize("value", ["0", "-5", "5001", "many", "12.5"])
def test_a_wrong_value_stops_the_start_with_the_variable_named(monkeypatch, value) -> None:
    monkeypatch.setenv("LIMIT_ROWS", value)
    with pytest.raises(RuntimeError, match="LIMIT_ROWS"):
        bounded("LIMIT_ROWS", 20, 5000)


def test_the_default_timeout_never_exceeds_the_maximum() -> None:
    assert run_limits.DEFAULT_TIMEOUT_SECONDS <= run_limits.MAX_TIMEOUT_SECONDS
