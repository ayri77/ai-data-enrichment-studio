"""Smoke CLI behavior is tested offline; CI must never call external APIs."""

from typing import Any

import pytest

from ai_data_enrichment.evaluation import smoke


@pytest.mark.parametrize("scenario", ["capitals", "mountains"])
def test_offline_smoke_runs_validated_job_with_provider_evidence(scenario: smoke.Scenario) -> None:
    result = smoke.run_smoke(scenario, "offline")
    assert result["mode"] == "offline"
    runs = result["runs"]
    assert isinstance(runs, list) and len(runs) == 1
    run: dict[str, Any] = runs[0]
    metrics = run["metrics"]
    assert metrics["total_rows"] == 2
    assert metrics["successful_rows"] == 2
    assert metrics["failed_rows"] == metrics["partial_rows"] == 0
    assert metrics["reused_operations"] > 0
    source = "open-meteo" if scenario == "capitals" else "wikidata"
    assert run["requests"][source]["transport_attempts"] == (2 if scenario == "capitals" else 4)
    assert run["requests"][source]["requests_blocked_by_budget"] == 0
    final = run["records"][0]["steps"][-1]
    assert final["status"] == "success"
    assert final["value"]["unit"] == ("km" if scenario == "capitals" else "m")
    assert final["evidence"]


@pytest.mark.parametrize("scenario", ["capitals", "mountains"])
def test_cache_mode_reuses_cross_job_sqlite_without_network(scenario: smoke.Scenario) -> None:
    result = smoke.run_smoke(scenario, "cache")
    runs = result["runs"]
    assert isinstance(runs, list) and len(runs) == 2
    before: dict[str, Any] = runs[0]
    after: dict[str, Any] = runs[1]
    provider = "open-meteo" if scenario == "capitals" else "wikidata"
    assert before["requests"][provider]["transport_attempts"] > 0
    assert after["requests"][provider]["transport_attempts"] == 0
    assert after["metrics"]["persistent_cache_hits"] >= (2 if scenario == "capitals" else 2)
    assert before["records"] == after["records"]


def test_live_requires_explicit_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoNetwork:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("No outbound client should be constructed.")

    monkeypatch.setattr(smoke, "UrllibJsonTransport", NoNetwork)
    with pytest.raises(ValueError, match="allow-network"):
        smoke.run_smoke("capitals", "live")
    result = smoke.run_smoke("capitals", "offline")
    assert result["mode"] == "offline"


def test_offline_scaffold_does_not_open_excel_or_generate_plans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("No network, planning or file reads expected.")

    monkeypatch.setattr(smoke, "UrllibJsonTransport", fail)
    for scenario in ("capitals", "mountains"):
        result = smoke.run_smoke(scenario, "offline")
        assert result["mode"] == "offline"
