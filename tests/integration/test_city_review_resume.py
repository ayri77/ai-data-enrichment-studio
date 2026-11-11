"""Two passes through the real executor: an ambiguous city, then a user-approved resume."""

from pathlib import Path
from typing import Any

import pytest
from tests.support.http import StubJsonTransport

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.job_models import InputRecord, JobExecutionResult
from ai_data_enrichment.execution.models import GeoPoint
from ai_data_enrichment.orchestration.live_executor import build_live_executor
from ai_data_enrichment.plan_run_worker import builtin_registry
from ai_data_enrichment.resolution.approvals import (
    ApprovalManifest,
    ResolutionApproval,
    workbook_sha256,
)
from ai_data_enrichment.resolution.resume import ResumeSelection, build_resume_selections

CAPITAL, NAMESAKE, DISTRICT = "2950159", "2950158", "2950157"
BERLINS = [
    {
        "id": int(location_id),
        "name": "Berlin",
        "country_code": "DE",
        "feature_code": feature_code,
        "admin1": admin1,
        "latitude": latitude,
        "longitude": 13.4,
    }
    for location_id, feature_code, admin1, latitude in [
        (CAPITAL, "PPLC", "State of Berlin", 52.52),
        (NAMESAKE, "PPL", "Schleswig-Holstein", 54.03),
        (DISTRICT, "PPLX", "Hamburg", 53.55),
    ]
]
PLAN = EnrichmentPlan.model_validate(
    {
        "version": "1.0",
        "steps": [
            {
                "id": "place",
                "capability": "geocode",
                "inputs": {
                    "city": {"kind": "column", "name": "city"},
                    "country": {"kind": "column", "name": "country"},
                },
                "parameters": {},
            },
            {
                "id": "distance",
                "capability": "geodesic_distance",
                "inputs": {
                    "point_a": {"kind": "step_output", "step_id": "place"},
                    "point_b": {"kind": "step_output", "step_id": "place"},
                },
                "parameters": {"unit": "km"},
            },
        ],
        "outputs": [],
        "assumptions": [],
    }
)
# Two rows share one query: the approval must reach both.
RECORDS = [
    InputRecord(record_id="Sheet1!2", values={"city": "Berlin", "country": "Germany"}),
    InputRecord(record_id="Sheet1!3", values={"city": "Berlin", "country": "Germany"}),
]


def run(
    transport: StubJsonTransport,
    *,
    job_id: str,
    selections: dict[tuple[str, str], ResumeSelection] | None = None,
    previous_job_id: str | None = None,
) -> JobExecutionResult:
    executor = build_live_executor(
        builtin_registry(),
        audit_capitals=True,
        resume_selections=selections,
        transport=transport,
    )
    return executor.execute(PLAN, RECORDS, job_id=job_id, previous_job_id=previous_job_id)


def first_pass(tmp_path: Path) -> tuple[JobExecutionResult, Path]:
    workbook = tmp_path / "source.xlsx"
    workbook.write_bytes(b"input-version-1")
    result = run(StubJsonTransport({"results": BERLINS}), job_id="job-1")
    assert result.metrics.successful_rows == 0
    return result, workbook


def manifest(result: JobExecutionResult, workbook: Path, entity_id: str) -> ApprovalManifest:
    report = result.resolution_report
    assert report is not None and len(report.operations) == 1
    operation = report.operations[0]
    assert operation.report.decision.status == "needs_review"
    assert {(link.record_id, link.step_id) for link in operation.record_links} == {
        ("Sheet1!2", "place"),
        ("Sheet1!3", "place"),
    }
    return ApprovalManifest(
        source_job_id=report.job_id,
        workbook_sha256=workbook_sha256(workbook),
        approvals=tuple(
            ResolutionApproval(
                record_id=link.record_id,
                step_id=link.step_id,
                operation_id=operation.operation_id,
                source="open-meteo",
                entity_id=entity_id,
            )
            for link in operation.record_links
        ),
    )


def selections_for(result: JobExecutionResult, workbook: Path, entity_id: str) -> Any:
    assert result.resolution_report is not None
    return build_resume_selections(
        manifest=manifest(result, workbook, entity_id),
        report=result.resolution_report,
        plan=PLAN,
        source_path=workbook,
    )


def test_approved_city_fills_every_affected_row_on_resume(tmp_path: Path) -> None:
    first, workbook = first_pass(tmp_path)
    transport = StubJsonTransport({"results": BERLINS})
    second = run(
        transport,
        job_id="job-2",
        selections=selections_for(first, workbook, NAMESAKE),
        previous_job_id="job-1",
    )
    assert second.metrics.successful_rows == 2
    for record in second.records:
        place = record.row_result.steps[0].result
        assert place.status == "success"
        assert place.value == GeoPoint(latitude=54.03, longitude=13.4)
    report = second.resolution_report
    assert report is not None and report.previous_job_id == "job-1"
    decisions = [operation.report.decision for operation in report.operations]
    assert decisions and all(d.basis == "user_override" for d in decisions)
    assert all(d.selected is not None and d.selected.entity_id == NAMESAKE for d in decisions)
    # Approved results are never shared through memoization: one fresh search per row.
    assert len(transport.requests) == 2


def test_resume_keeps_a_row_unresolved_when_the_location_is_gone(tmp_path: Path) -> None:
    first, workbook = first_pass(tmp_path)
    second = run(
        StubJsonTransport({"results": [BERLINS[0], BERLINS[2]]}),
        job_id="job-2",
        selections=selections_for(first, workbook, NAMESAKE),
        previous_job_id="job-1",
    )
    assert second.metrics.successful_rows == 0
    assert all(
        record.row_result.steps[0].result.error_code == "APPROVAL_NOT_REVERIFIED"
        for record in second.records
    )


def test_excluded_or_foreign_candidates_cannot_be_approved(tmp_path: Path) -> None:
    first, workbook = first_pass(tmp_path)
    with pytest.raises(ValueError, match="excluded by the source decision"):
        selections_for(first, workbook, DISTRICT)
    with pytest.raises(ValueError, match="not source-verified"):
        selections_for(first, workbook, "42")
    wikidata = manifest(first, workbook, NAMESAKE).model_copy(
        update={
            "approvals": tuple(
                item.model_copy(update={"source": "wikidata", "entity_id": "Q64"})
                for item in manifest(first, workbook, NAMESAKE).approvals
            )
        }
    )
    assert first.resolution_report is not None
    with pytest.raises(ValueError):
        build_resume_selections(
            manifest=wikidata, report=first.resolution_report, plan=PLAN, source_path=workbook
        )


def test_changed_workbook_or_plan_rejects_the_resume(tmp_path: Path) -> None:
    first, workbook = first_pass(tmp_path)
    approvals = manifest(first, workbook, NAMESAKE)
    assert first.resolution_report is not None
    changed = PLAN.model_copy(deep=True)
    changed.steps[0].parameters["required_role"] = "country_capital"
    # The plan is validated when selections are built and again by the approved service.
    selections = build_resume_selections(
        manifest=approvals, report=first.resolution_report, plan=changed, source_path=workbook
    )
    executor = build_live_executor(
        builtin_registry(),
        audit_capitals=True,
        resume_selections=selections,
        transport=StubJsonTransport({"results": BERLINS}),
    )
    with pytest.raises(ValueError, match="same Open-Meteo geocoding step"):
        executor.execute(changed, RECORDS, job_id="job-2")
    workbook.write_bytes(b"input-version-2")
    with pytest.raises(ValueError, match="Source workbook changed"):
        build_resume_selections(
            manifest=approvals, report=first.resolution_report, plan=PLAN, source_path=workbook
        )


def test_city_approvals_require_audited_city_resolution(tmp_path: Path) -> None:
    first, workbook = first_pass(tmp_path)
    with pytest.raises(ValueError, match="audited city resolution"):
        build_live_executor(
            builtin_registry(),
            resume_selections=selections_for(first, workbook, NAMESAKE),
            transport=StubJsonTransport({"results": BERLINS}),
        )
