"""Offline provenance checks for the user approval contract."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_data_enrichment.resolution.approvals import (
    ApprovalManifest,
    ResolutionApproval,
    validate_approval_manifest,
    workbook_sha256,
)
from ai_data_enrichment.resolution.models import ResolutionJobReport


def _source_report() -> ResolutionJobReport:
    payload = {
        "job_id": "job-123",
        "status": "awaiting_review",
        "total_rows": 1,
        "processed_rows": 1,
        "operations": [
            {
                "operation_id": "operation-abc",
                "record_links": [{"record_id": "Mountains!2", "step_id": "resolve"}],
                "report": {
                    "policy": {"policy_id": "mountain_strict", "version": "1.0"},
                    "candidate_set": {
                        "query": {"name": "Mount Everest", "entity_type": "mountain"},
                        "provider_id": "wikidata",
                        "search_complete": True,
                        "verification_complete": False,
                        "incomplete_reason": "candidate_verification_incomplete",
                        "candidates": [
                            {
                                "reference": {"source": "wikidata", "entity_id": "Q513"},
                                "label": "Mount Everest",
                                "entity_type": "mountain",
                                "verification": "verified",
                                "evidence": [{"source": "wikidata", "reference": "Q513:P31,P17"}],
                            },
                            {
                                "reference": {"source": "wikidata", "entity_id": "Q42"},
                                "label": "Mount Everest",
                                "entity_type": "unknown",
                                "verification": "unchecked",
                            },
                        ],
                    },
                    "decision": {
                        "status": "incomplete_verification",
                        "reason_codes": ["source_incomplete"],
                        "explanation": "Some candidates were not verified.",
                    },
                },
            }
        ],
    }
    return ResolutionJobReport.model_validate_json(json.dumps(payload))


def _manifest(source_path: Path, *, entity_id: str = "Q513") -> ApprovalManifest:
    return ApprovalManifest(
        source_job_id="job-123",
        workbook_sha256=workbook_sha256(source_path),
        approvals=(
            ResolutionApproval(
                record_id="Mountains!2",
                step_id="resolve",
                operation_id="operation-abc",
                source="wikidata",
                entity_id=entity_id,
            ),
        ),
    )


def test_approval_accepts_only_verified_candidate_from_original_row(tmp_path: Path) -> None:
    workbook = tmp_path / "source.xlsx"
    workbook.write_bytes(b"input-version-1")
    accepted = validate_approval_manifest(
        _manifest(workbook), _source_report(), source_path=workbook
    )
    assert accepted[("Mountains!2", "resolve")].entity_id == "Q513"


def test_unchecked_candidate_cannot_be_approved(tmp_path: Path) -> None:
    workbook = tmp_path / "source.xlsx"
    workbook.write_bytes(b"input-version-1")
    with pytest.raises(ValueError, match="not source-verified"):
        validate_approval_manifest(
            _manifest(workbook, entity_id="Q42"), _source_report(), source_path=workbook
        )


def test_modified_workbook_invalidates_approval(tmp_path: Path) -> None:
    workbook = tmp_path / "source.xlsx"
    workbook.write_bytes(b"input-version-1")
    manifest = _manifest(workbook)
    workbook.write_bytes(b"input-version-2")
    with pytest.raises(ValueError, match="Source workbook changed"):
        validate_approval_manifest(manifest, _source_report(), source_path=workbook)


def test_wrong_row_or_job_is_rejected(tmp_path: Path) -> None:
    workbook = tmp_path / "source.xlsx"
    workbook.write_bytes(b"input-version-1")
    manifest = _manifest(workbook)
    wrong_row = manifest.model_copy(
        update={
            "approvals": (manifest.approvals[0].model_copy(update={"record_id": "Mountains!3"}),)
        }
    )
    with pytest.raises(ValueError, match="does not belong"):
        validate_approval_manifest(wrong_row, _source_report(), source_path=workbook)
    wrong_job = manifest.model_copy(update={"source_job_id": "other-job"})
    with pytest.raises(ValueError, match="source job"):
        validate_approval_manifest(wrong_job, _source_report(), source_path=workbook)


def test_duplicate_and_malformed_approvals_fail_validation(tmp_path: Path) -> None:
    workbook = tmp_path / "source.xlsx"
    workbook.write_bytes(b"input-version-1")
    manifest = _manifest(workbook)
    payload = manifest.model_dump()
    payload["approvals"] = [payload["approvals"][0], payload["approvals"][0]]
    with pytest.raises(ValidationError, match="multiple approvals"):
        ApprovalManifest.model_validate(payload)
    with pytest.raises(ValidationError, match="valid QID"):
        ResolutionApproval(
            record_id="Mountains!2",
            step_id="resolve",
            operation_id="operation-abc",
            source="wikidata",
            entity_id="not-a-QID",
        )


@pytest.mark.parametrize("entity_id", ["Q64", "0", "-5", "12a", ""])
def test_open_meteo_approvals_require_a_positive_numeric_location_id(entity_id: str) -> None:
    with pytest.raises(ValidationError):
        ResolutionApproval(
            record_id="Sheet1!2",
            step_id="geocode_to",
            operation_id="operation-abc",
            source="open-meteo",
            entity_id=entity_id,
        )
    assert ResolutionApproval(
        record_id="Sheet1!2",
        step_id="geocode_to",
        operation_id="operation-abc",
        source="open-meteo",
        entity_id="2950159",
    )
