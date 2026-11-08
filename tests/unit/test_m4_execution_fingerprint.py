"""Tests for deterministic enrichment execution fingerprints."""

from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.job_models import InputRecord
from ai_data_enrichment.orchestration.agent.fingerprint import (
    calculate_execution_fingerprint,
)


def make_plan() -> EnrichmentPlan:
    """Build a structurally valid plan for fingerprint tests."""
    return EnrichmentPlan.model_validate(
        {
            "version": "1.0",
            "steps": [
                {
                    "id": "resolve",
                    "capability": "resolve_entity",
                    "inputs": {
                        "name": {
                            "kind": "column",
                            "name": "Mountain",
                        }
                    },
                    "parameters": {"entity_type": "mountain"},
                }
            ],
            "outputs": [],
            "assumptions": [],
        }
    )


def make_profile() -> DatasetProfile:
    return DatasetProfile(
        dataset_id="mountains",
        name="Mountains",
        columns=[
            ColumnProfile(
                name="Mountain",
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
            ),
        ],
    )


def make_records() -> tuple[InputRecord, ...]:
    return (
        InputRecord(
            record_id="row-1",
            values={"Mountain": "Everest", "Country": "Nepal"},
        ),
        InputRecord(
            record_id="row-2",
            values={"Mountain": "K2", "Country": "Pakistan"},
        ),
    )


def fingerprint(
    *,
    plan: EnrichmentPlan | None = None,
    profile: DatasetProfile | None = None,
    records: tuple[InputRecord, ...] | None = None,
) -> str:
    return calculate_execution_fingerprint(
        plan=plan if plan is not None else make_plan(),
        dataset_profile=profile if profile is not None else make_profile(),
        records=records if records is not None else make_records(),
    )


def test_identical_inputs_produce_same_fingerprint() -> None:
    """Equivalent execution inputs must produce the same SHA-256."""
    first = fingerprint()
    second = fingerprint()

    assert first == second
    assert len(first) == 64


def test_dictionary_key_order_does_not_change_fingerprint() -> None:
    """JSON object key ordering must not affect execution identity."""
    records = make_records()

    reordered = tuple(
        record.model_copy(update={"values": dict(reversed(list(record.values.items())))})
        for record in records
    )

    assert fingerprint(records=records) == fingerprint(records=reordered)


def test_changed_record_changes_fingerprint() -> None:
    """Changing source data must invalidate execution identity."""
    records = make_records()

    changed = (
        records[0].model_copy(update={"values": {"Mountain": "Mont Blanc", "Country": "France"}}),
        records[1],
    )

    assert fingerprint(records=records) != fingerprint(records=changed)


def test_changed_plan_or_record_order_changes_fingerprint() -> None:
    """Plan modifications and row order must affect execution identity."""
    plan = make_plan()

    modified_plan = plan.model_copy(
        update={"assumptions": ["Use only verified mountain identities."]}
    )

    records = make_records()

    assert fingerprint(plan=plan) != fingerprint(plan=modified_plan)
    assert fingerprint(records=records) != fingerprint(records=tuple(reversed(records)))
