"""Fast offline test of the *same* acceptance harness used by CI for 1000 rows."""

import pytest

from ai_data_enrichment.evaluation.m2_benchmark import run_acceptance, run_scenario


def test_validated_both_scenarios_and_adversarial_abstention_with_evidence() -> None:
    report = run_acceptance(rows=8)
    assert report["kind"] == "m2-controlled-offline-acceptance"
    results = report["benchmarks"]
    assert isinstance(results, list)
    assert len(results) == 4
    capitals, mountains, strict, unknown = results
    assert capitals["rows"] == 8
    assert capitals["successful_rows"] == 8
    assert capitals["logical_operations"] == 24
    assert capitals["executed_handler_calls"] == 4
    assert capitals["reused_operations"] == 20
    assert capitals["admitted_transport_attempts"] == 2
    assert capitals["audit_row_step_links"] == 16
    assert capitals["resolution_decisions"] == {"auto_resolved": 1, "resolved": 1}

    assert mountains["rows"] == 8
    assert mountains["successful_rows"] == 8
    assert mountains["logical_operations"] == 16
    assert mountains["executed_handler_calls"] == 2
    assert mountains["reused_operations"] == 14
    assert mountains["admitted_transport_attempts"] == 5
    assert mountains["audit_row_step_links"] == 8
    assert mountains["resolution_decisions"] == {"resolved": 1}

    assert strict["partial_rows"] == 1
    assert strict["resolution_decisions"]["needs_review"] == 1
    assert unknown["failed_rows"] == 1
    assert unknown["resolution_decisions"]["incomplete_verification"] == 1


@pytest.mark.parametrize(("rows", "scenario"), [(0, "capitals"), (1001, "mountains")])
def test_benchmark_refuses_out_of_scope_row_counts(rows: int, scenario: str) -> None:
    with pytest.raises(ValueError, match="1 to 1000"):
        run_scenario(scenario, rows=rows)  # type: ignore[arg-type]
