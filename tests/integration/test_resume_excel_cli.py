"""Offline Review -> Approve -> Resume through the real CLI, DAG and Excel writer."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from tests.support.wikidata import entity_statement
from typer.testing import CliRunner

from ai_data_enrichment.cli import app
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.providers.http_transport import UrllibJsonTransport
from ai_data_enrichment.resolution.approvals import (
    ApprovalManifest,
    ResolutionApproval,
    workbook_sha256,
)
from ai_data_enrichment.resolution.models import ResolutionJobReport


class WikidataFixture:
    def __init__(self, *, name: str, qid: str) -> None:
        self.name = name
        self.qid = qid
        self.requests: list[dict[str, str]] = []
        self.country_code = "NP"
        self.type_id = "Q8502"

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        assert url == "https://www.wikidata.org/w/api.php"
        assert timeout_seconds == 10.0
        self.requests.append(dict(params))
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": qid, "label": self.name} for qid in (self.qid, "Q999999")]}
        assert params["action"] == "wbgetentities"
        qid = params["ids"]
        if qid == self.qid:
            claims = {
                "P31": [entity_statement(self.type_id)],
                "P17": [entity_statement("Q837")],
                "P2044": [
                    {
                        "rank": "normal",
                        "mainsnak": {
                            "snaktype": "value",
                            "datavalue": {
                                "type": "quantity",
                                "value": {
                                    "amount": "+8848.86",
                                    "unit": "http://www.wikidata.org/entity/Q11573",
                                },
                            },
                        },
                    }
                ],
            }
        elif qid == "Q837":
            claims = {
                "P297": [
                    {
                        "rank": "normal",
                        "mainsnak": {
                            "snaktype": "value",
                            "datavalue": {"type": "string", "value": self.country_code},
                        },
                    }
                ]
            }
        else:
            assert qid == "Q999999"
            claims = {}  # An unchecked alternative keeps the original job in review.
        return {"entities": {qid: {"id": qid, "claims": claims}}}


@dataclass
class ResumeCase:
    source: Path
    plan: Path
    columns: Path
    report: Path
    approvals: Path
    rows: int
    transport: WikidataFixture

    def args(self, output: Path, *, resume: bool = True) -> list[str]:
        args = [
            "run",
            str(self.source),
            "--plan",
            str(self.plan),
            "--sheet",
            "Mountains",
            "--range",
            f"A1:B{self.rows + 1}",
            "--columns-file",
            str(self.columns),
            "--output",
            str(output),
            "--allow-network",
        ]
        if resume:
            args.extend(
                [
                    "--approvals-file",
                    str(self.approvals),
                    "--source-report",
                    str(self.report),
                ]
            )
        return args


def prepare_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: int = 1,
    approved_row: int = 2,
    name: str = "Nuptse",
    qid: str = "Q12345",
) -> ResumeCase:
    transport = WikidataFixture(name=name, qid=qid)
    # Every HTTP request, including unexpected ones, stays inside this fixture.
    monkeypatch.setattr(UrllibJsonTransport, "get_json", transport.get_json)
    source = tmp_path / "source.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Mountains"
    sheet.append(["Mountain", "Country"])
    for _ in range(rows):
        sheet.append([name, "Nepal"])
    workbook.save(source)
    workbook.close()
    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": ColumnReference(kind="column", name="Mountain"),
                    "country": ColumnReference(kind="column", name="Country"),
                },
                parameters={"entity_type": "mountain"},
            ),
            PlanStep(
                id="height",
                capability="lookup_property",
                inputs={"entity": StepOutputReference(kind="step_output", step_id="resolve")},
                parameters={"property": "elevation"},
            ),
        ],
        outputs=[
            OutputMapping(step_id="height", value_field="value", target_column="elevation_m"),
            OutputMapping(step_id="height", value_field="unit", target_column="elevation_unit"),
        ],
        assumptions=[],
    )
    case = ResumeCase(
        source=source,
        plan=tmp_path / "plan.json",
        columns=tmp_path / "columns.json",
        report=tmp_path / "original.resolution.json",
        approvals=tmp_path / "approvals.json",
        rows=rows,
        transport=transport,
    )
    case.plan.write_text(plan.model_dump_json(), encoding="utf-8")
    case.columns.write_text(json.dumps({"elevation_m": "C", "elevation_unit": "D"}))
    first = CliRunner().invoke(app, case.args(tmp_path / "original.xlsx", resume=False))
    assert first.exit_code == 0, first.output
    assert f"Rows: {rows}, success: 0, partial: 0, failed: {rows}" in first.output
    report = ResolutionJobReport.model_validate_json(case.report.read_text())
    operation = next(
        item
        for item in report.operations
        if any(link.record_id == f"Mountains!{approved_row}" for link in item.record_links)
    )
    assert operation.report.decision.status == "incomplete_verification"
    manifest = ApprovalManifest(
        source_job_id=report.job_id,
        workbook_sha256=workbook_sha256(source),
        approvals=(
            ResolutionApproval(
                record_id=f"Mountains!{approved_row}",
                step_id="resolve",
                operation_id=operation.operation_id,
                source="wikidata",
                entity_id=qid,
            ),
        ),
    )
    case.approvals.write_text(manifest.model_dump_json(), encoding="utf-8")
    transport.requests.clear()
    return case


@pytest.mark.parametrize(("name", "qid"), [("Nuptse", "Q12345"), ("Mount Everest", "Q513")])
def test_resume_verifies_directly_then_writes_property_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, qid: str
) -> None:
    case = prepare_case(tmp_path, monkeypatch, name=name, qid=qid)
    source_before = case.source.read_bytes()
    report_before = case.report.read_bytes()
    output = tmp_path / "resumed.xlsx"
    result = CliRunner().invoke(app, case.args(output))
    assert result.exit_code == 0, result.output
    assert "Rows: 1, success: 1, partial: 0, failed: 0" in result.output
    assert [request["action"] for request in case.transport.requests] == ["wbgetentities"] * 3
    assert [request["ids"] for request in case.transport.requests] == [qid, "Q837", qid]
    workbook = load_workbook(output)
    try:
        sheet = workbook["Mountains"]
        assert [sheet[cell].value for cell in ("C1", "D1", "C2", "D2")] == [
            "elevation_m",
            "elevation_unit",
            8848.86,
            "m",
        ]
    finally:
        workbook.close()
    report = ResolutionJobReport.model_validate_json(
        output.with_suffix(".resolution.json").read_text()
    )
    original = ResolutionJobReport.model_validate_json(report_before)
    assert report.previous_job_id == original.job_id
    assert report.job_id != original.job_id
    assert report.status == "completed"
    operation = report.operations[0]
    assert operation.report.decision.basis == "user_override"
    assert operation.report.decision.selected is not None
    assert operation.report.decision.selected.entity_id == qid
    candidate = operation.report.candidate_set.candidates[0]
    assert candidate.verification == "verified"
    assert candidate.evidence
    assert all(evidence.source == "wikidata" for evidence in candidate.evidence)
    assert "P31" in candidate.evidence[0].reference
    assert "P17" in candidate.evidence[0].reference
    assert "P297" in candidate.evidence[0].reference
    assert case.source.read_bytes() == source_before
    assert case.report.read_bytes() == report_before


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("record_id", "Mountains!99", "does not belong"),
        ("step_id", "height", "does not belong"),
        ("entity_id", "Q999999", "not source-verified"),
        ("entity_id", "Q888888", "not source-verified"),
    ],
)
def test_invalid_approval_fails_before_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: str, error: str
) -> None:
    case = prepare_case(tmp_path, monkeypatch)
    manifest = json.loads(case.approvals.read_text())
    manifest["approvals"][0][field] = value
    case.approvals.write_text(json.dumps(manifest))
    output = tmp_path / "invalid.xlsx"
    result = CliRunner().invoke(app, case.args(output))
    assert result.exit_code == 2
    assert error in result.output
    assert not case.transport.requests
    assert not output.exists()


def test_modified_workbook_fails_before_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_case(tmp_path, monkeypatch)
    workbook = load_workbook(case.source)
    workbook["Mountains"]["A2"] = "Changed mountain"
    workbook.save(case.source)
    workbook.close()
    output = tmp_path / "changed.xlsx"
    result = CliRunner().invoke(app, case.args(output))
    assert result.exit_code == 2
    assert "Source workbook changed" in result.output
    assert not case.transport.requests
    assert not output.exists()


@pytest.mark.parametrize("approved_row", [2, 3])
def test_identical_rows_keep_approval_scoped_in_either_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approved_row: int
) -> None:
    case = prepare_case(tmp_path, monkeypatch, rows=2, approved_row=approved_row)
    output = tmp_path / "mixed.xlsx"
    result = CliRunner().invoke(app, case.args(output))
    assert result.exit_code == 0, result.output
    assert "Rows: 2, success: 1, partial: 0, failed: 1" in result.output
    assert sum(r["action"] == "wbsearchentities" for r in case.transport.requests) == 1
    report = ResolutionJobReport.model_validate_json(
        output.with_suffix(".resolution.json").read_text()
    )
    decisions = {
        link.record_id: operation.report.decision
        for operation in report.operations
        for link in operation.record_links
    }
    assert decisions[f"Mountains!{approved_row}"].basis == "user_override"
    assert decisions[f"Mountains!{5 - approved_row}"].status == "incomplete_verification"
    workbook = load_workbook(output)
    try:
        assert workbook["Mountains"][f"C{approved_row}"].value == 8848.86
        assert workbook["Mountains"][f"C{5 - approved_row}"].value is None
    finally:
        workbook.close()


@pytest.mark.parametrize("constraint", ["name", "country"])
def test_changed_plan_query_on_later_approval_rejects_entire_job_before_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constraint: str
) -> None:
    case = prepare_case(tmp_path, monkeypatch, rows=2, approved_row=3)
    plan = json.loads(case.plan.read_text())
    plan["steps"][0]["inputs"][constraint] = {
        "kind": "literal",
        "value": "Other mountain" if constraint == "name" else "China",
    }
    case.plan.write_text(json.dumps(plan))
    result = CliRunner().invoke(app, case.args(tmp_path / "changed-plan.xlsx"))
    assert result.exit_code == 2
    assert "differs from approved source query" in result.output
    assert not case.transport.requests


@pytest.mark.parametrize("drift", ["country", "type"])
def test_approval_still_requires_fresh_country_and_type_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    case = prepare_case(tmp_path, monkeypatch)
    if drift == "country":
        case.transport.country_code = "CN"
    else:
        case.transport.type_id = "Q11424"
    output = tmp_path / "drift.xlsx"
    result = CliRunner().invoke(app, case.args(output))
    assert result.exit_code == 0, result.output
    assert "success: 0" in result.output
    assert all(r["action"] == "wbgetentities" for r in case.transport.requests)
    assert sum(r.get("ids") == case.transport.qid for r in case.transport.requests) == 1
    report = ResolutionJobReport.model_validate_json(
        output.with_suffix(".resolution.json").read_text()
    )
    assert report.operations[0].report.decision.selected is None
    candidate = report.operations[0].report.candidate_set.candidates[0]

    expected_verification = "rejected" if drift == "type" else "unchecked"

    assert candidate.verification == expected_verification

    candidate_set = report.operations[0].report.candidate_set

    assert candidate_set.verification_complete is (drift == "type")


def test_no_approval_arguments_preserves_normal_discovery_and_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_case(tmp_path, monkeypatch)
    result = CliRunner().invoke(app, case.args(tmp_path / "normal.xlsx", resume=False))
    assert result.exit_code == 0, result.output
    assert "success: 0" in result.output
    assert case.transport.requests[0]["action"] == "wbsearchentities"
    report = ResolutionJobReport.model_validate_json(
        (tmp_path / "normal.resolution.json").read_text()
    )
    assert report.previous_job_id is None
    assert report.operations[0].report.decision.status == "incomplete_verification"


@pytest.mark.parametrize("option", ["--approvals-file", "--source-report"])
def test_resume_cli_requires_both_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, option: str
) -> None:
    case = prepare_case(tmp_path, monkeypatch)
    args = case.args(tmp_path / "unpaired.xlsx", resume=False)
    args.extend([option, str(case.approvals if option == "--approvals-file" else case.report)])
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "must be supplied together" in result.output
    assert not case.transport.requests


@pytest.mark.parametrize("existing", ["workbook", "audit"])
def test_resume_preserves_existing_output_before_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: str
) -> None:
    case = prepare_case(tmp_path, monkeypatch)
    output = tmp_path / "existing.xlsx"
    target = output if existing == "workbook" else output.with_suffix(".resolution.json")
    target.write_bytes(b"keep this output")
    result = CliRunner().invoke(app, case.args(output))
    assert result.exit_code == 2
    assert target.read_bytes() == b"keep this output"
    assert not case.transport.requests
