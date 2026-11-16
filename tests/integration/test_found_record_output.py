"""A found record written to Excel: per-slot values, gaps and provenance."""

from pathlib import Path

from openpyxl import Workbook, load_workbook

from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan, OutputMapping, PlanStep
from ai_data_enrichment.execution.job_models import (
    JobExecutionMetrics,
    JobExecutionResult,
    RecordExecution,
)
from ai_data_enrichment.execution.models import (
    Evidence,
    FoundRecord,
    FoundValue,
    GeoPoint,
    OperationResult,
    RowExecutionResult,
    StepExecutionResult,
)
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.output.excel_output_mapping import (
    EMPTY_FIELD_CODE,
    build_output_value_writes,
    empty_output_fields,
)
from ai_data_enrichment.output.provenance import (
    SHEET_HEADER,
    annotate_workbook,
    build_cell_provenance,
    note_text,
)

LINK_A = "https://example.org/a"
LINK_B = "https://example.org/b"
LINK_C = "https://example.org/c"


def lookup_plan() -> EnrichmentPlan:
    return EnrichmentPlan(
        version="1",
        steps=[PlanStep(id="find", capability="web_lookup", inputs={}, parameters={})],
        outputs=[
            OutputMapping(step_id="find", value_field="value_1", target_column="capital"),
            OutputMapping(step_id="find", value_field="value_2", target_column="area_km2"),
            OutputMapping(step_id="find", value_field="value_3", target_column="note"),
        ],
        assumptions=[],
    )


def found_record() -> FoundRecord:
    return FoundRecord(
        slots={
            "value_1": FoundValue(
                value="Hamburg",
                basis="stated",
                sources=(LINK_A, LINK_B),
                quote="Hamburg is a city state.",
            ),
            "value_2": FoundValue(value=755.2, basis="concluded", sources=(LINK_C,)),
            "value_3": FoundValue(value=None, basis=None, sources=(LINK_A,)),
        }
    )


def job_for(result: OperationResult) -> JobExecutionResult:
    return JobExecutionResult(
        records=(
            RecordExecution(
                record_id="a",
                row_result=RowExecutionResult(
                    steps=(StepExecutionResult(step_id="find", result=result),)
                ),
            ),
        ),
        metrics=JobExecutionMetrics(
            total_rows=1, successful_rows=1, logical_operations=1, executed_handler_calls=1
        ),
    )


SELECTION = TableSelection("Cities", "B6:D11")
COLUMNS = {"capital": "E", "area_km2": "F", "note": "G"}


def writes_for(job: JobExecutionResult) -> dict[str, str | float]:
    return build_output_value_writes(
        lookup_plan(),
        job,
        SELECTION,
        row_by_record_id={"a": 7},
        output_columns=COLUMNS,
    )


def test_slots_are_written_as_text_and_number_and_a_gap_is_reported() -> None:
    job = job_for(OperationResult(status="success", value=found_record()))
    assert writes_for(job) == {"E7": "Hamburg", "F7": 755.2}
    assert EMPTY_FIELD_CODE == "VALUE_NOT_GIVEN"
    assert empty_output_fields(lookup_plan(), job) == {"a": {"note": "value_3"}}


def test_a_record_of_only_gaps_writes_nothing_and_reports_every_gap() -> None:
    gaps = FoundRecord(
        slots={
            name: FoundValue(value=None, basis=None) for name in ("value_1", "value_2", "value_3")
        }
    )
    job = job_for(OperationResult(status="success", value=gaps))
    assert writes_for(job) == {}
    assert empty_output_fields(lookup_plan(), job) == {
        "a": {"capital": "value_1", "area_km2": "value_2", "note": "value_3"}
    }


def entries_for(job: JobExecutionResult):  # type: ignore[no-untyped-def]
    return build_cell_provenance(
        lookup_plan(),
        job,
        row_by_record_id={"a": 7},
        output_columns=COLUMNS,
        written=writes_for(job),
    )


def test_each_cell_has_the_links_and_basis_of_its_own_slot() -> None:
    # The step's evidence is the union of everything; a cell must not show it.
    result = OperationResult(
        status="success",
        value=found_record(),
        evidence=(Evidence(source="openai_web_search", reference="https://union.example"),),
    )
    first, second = entries_for(job_for(result))
    assert (first.cell, first.source, first.trust) == ("E7", "openai_web_search", "ai_found")
    assert first.reference == f"{LINK_A}; {LINK_B}" and first.basis == "stated"
    assert first.quote == "Hamburg is a city state."
    assert (second.cell, second.reference, second.basis, second.quote) == (
        "F7",
        LINK_C,
        "concluded",
        "",
    )
    assert note_text(first).splitlines() == [
        "Source: OpenAI web search",
        f"Reference: {LINK_A}; {LINK_B}",
        "Kind of trust: found by an AI web search, not verified",
        "Basis: stated on the page",
        "Quote: Hamburg is a city state.",
    ]
    assert note_text(second).splitlines() == [
        "Source: OpenAI web search",
        f"Reference: {LINK_C}",
        "Kind of trust: found by an AI web search, not verified",
        "Basis: concluded by the model, not stated on the page",
    ]


def test_a_slot_that_was_not_found_gets_no_provenance() -> None:
    entries = entries_for(job_for(OperationResult(status="success", value=found_record())))
    assert [entry.cell for entry in entries] == ["E7", "F7"]


def workbook(tmp_path: Path) -> Path:
    path = tmp_path / "book.xlsx"
    book = Workbook()
    book.active.title = "Cities"  # type: ignore[union-attr]
    book.save(path)
    return path


def test_notes_and_sheet_carry_the_basis_without_changing_the_layout(tmp_path: Path) -> None:
    entries = entries_for(job_for(OperationResult(status="success", value=found_record())))
    notes = workbook(tmp_path)
    annotate_workbook(notes, sheet_name="Cities", mode="notes", entries=entries)
    book = load_workbook(notes)
    comment = book["Cities"]["E7"].comment
    assert comment is not None and "Basis: stated on the page" in comment.text
    book.close()

    sheet = workbook(tmp_path)
    annotate_workbook(sheet, sheet_name="Cities", mode="sheet", entries=entries)
    book = load_workbook(sheet)
    rows = list(book["Sources"].iter_rows(values_only=True))
    assert rows[0] == SHEET_HEADER and len(SHEET_HEADER) == 8
    assert rows[1] == (
        "Cities",
        "E7",
        "capital",
        "Hamburg",
        "web_lookup",
        "OpenAI web search",
        f"{LINK_A}; {LINK_B} | Quote: Hamburg is a city state.",
        "found by an AI web search, not verified (stated on the page)",
    )
    assert rows[2][6] == LINK_C
    assert rows[2][7] == (
        "found by an AI web search, not verified (concluded by the model, not stated on the page)"
    )
    book.close()


def test_an_existing_kind_keeps_its_note_and_sheet_row(tmp_path: Path) -> None:
    plan = EnrichmentPlan(
        version="1",
        steps=[PlanStep(id="geo", capability="geocode", inputs={}, parameters={})],
        outputs=[OutputMapping(step_id="geo", value_field="latitude", target_column="lat")],
        assumptions=[],
    )
    job = JobExecutionResult(
        records=(
            RecordExecution(
                record_id="a",
                row_result=RowExecutionResult(
                    steps=(
                        StepExecutionResult(
                            step_id="geo",
                            result=OperationResult(
                                status="success",
                                value=GeoPoint(latitude=53.55, longitude=10.0),
                                evidence=(
                                    Evidence(source="open-meteo", reference="geonames:2911298"),
                                    Evidence(source="open-meteo", reference="second"),
                                ),
                            ),
                        ),
                    )
                ),
            ),
        ),
        metrics=JobExecutionMetrics(
            total_rows=1, successful_rows=1, logical_operations=1, executed_handler_calls=1
        ),
    )
    (entry,) = build_cell_provenance(
        plan, job, row_by_record_id={"a": 7}, output_columns={"lat": "E"}, written={"E7": 53.55}
    )
    assert entry.basis == "" and entry.quote == ""
    assert note_text(entry) == (
        "Source: Open-Meteo\n"
        "Reference: geonames:2911298; second\n"
        "Kind of trust: a name matched to a record of the source"
    )
    path = workbook(tmp_path)
    annotate_workbook(path, sheet_name="Cities", mode="sheet", entries=[entry])
    book = load_workbook(path)
    rows = list(book["Sources"].iter_rows(values_only=True))
    assert rows[1] == (
        "Cities",
        "E7",
        "lat",
        53.55,
        "geocode",
        "Open-Meteo",
        "geonames:2911298; second",
        "a name matched to a record of the source",
    )
    book.close()
