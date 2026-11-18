"""The web lookup fallback from a request to a result workbook, offline.

A request is turned into a plan, validated by the real validator with the registry made
for that plan, and run by ``ExcelEnrichmentRunner`` with the real handler, reply reader
and workbook writer. Only the API object is fake: nothing here can reach a network.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook
from tests.support.web_lookup import (
    PAGE,
    PAGE_2,
    RowApi,
    empty_reply,
    good_reply,
    response,
    slot,
)

from ai_data_enrichment.capabilities.fallback import (
    FallbackRequest,
    FallbackTarget,
    build_fallback_plan,
    fallback_registry,
)
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.orchestration.excel_runner import (
    ExcelEnrichmentResult,
    ExcelEnrichmentRunner,
)
from ai_data_enrichment.orchestration.live_executor import WebLookupOptions, build_live_executor
from ai_data_enrichment.output.excel_output_mapping import empty_output_fields
from ai_data_enrichment.output.provenance import SHEET_HEADER, SourcesMode
from ai_data_enrichment.providers.web_lookup import WebLookupUsage
from ai_data_enrichment.resolution.sqlite_cache import SQLiteResolutionCache
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy, PlanValidator

SHEET = "Mountains"
KEY = "sk-test-secret"
SENTINEL = "SENTINEL-RAW-REPLY"
TRUST = "found by an AI web search, not verified"


def source_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = SHEET
    sheet.append(["Mountain", "Country", "Rank", "Notes"])
    sheet.append(["Everest", "Nepal", 1, None])
    sheet.append(["Lhotse", "Nepal", None, None])  # the numeric context cell is empty
    sheet.append(["K2", "Pakistan", 2, "keep me"])  # the existing target is filled
    sheet.append(["Nowhere", "Nowhere", 3, None])
    sheet.append(["Everest", "Nepal", 1, None])  # the same row again
    sheet.append(["Broken", "Nowhere", 4, None])
    workbook.save(path)
    workbook.close()


K2 = {
    "value_1": slot(unit=None),  # no height given
    "value_2": slot(value="Italian expedition", basis="stated", sources=[PAGE], quote="1954"),
    "value_3": slot(value=True, basis="stated", sources=[PAGE_2]),
}


def answer(kwargs: dict[str, Any]) -> Any:
    text: str = kwargs["input"]
    if "- Mountain: K2" in text:
        return response(K2)
    if "- Mountain: Nowhere" in text:
        return response(empty_reply())
    if "- Mountain: Broken" in text:
        return response(f"{SENTINEL} {{not json")
    return response(good_reply())


REQUEST = FallbackRequest(
    question="Find facts about this mountain.",
    context_columns=("Mountain", "Country", "Rank"),
    targets=(
        FallbackTarget(column="Height", kind="number", unit="m"),
        FallbackTarget(column="Notes", kind="text"),
        FallbackTarget(column="Climbed in winter", kind="yes_no"),
    ),
)
OUTPUT_COLUMNS = {"Notes": "D", "Height": "E", "Climbed in winter": "F"}


@dataclass
class Run:
    source: Path
    output: Path
    plan: EnrichmentPlan
    result: ExcelEnrichmentResult
    api: RowApi
    usage: WebLookupUsage


def run_fallback(
    tmp_path: Path,
    mode: SourcesMode,
    *,
    request: FallbackRequest = REQUEST,
    build: Any = source_workbook,
    table: str = "A1:D7",
    columns: dict[str, str] = OUTPUT_COLUMNS,
    api: RowApi | None = None,
    reuse: tuple[SQLiteOperationCache, SQLiteResolutionCache] | None = None,
) -> Run:
    source = tmp_path / "source.xlsx"
    build(source)
    selection = TableSelection(SHEET, table)
    profile = profile_worksheet(source, SHEET, "d", selection=selection)
    plan = build_fallback_plan(request, profile.columns)
    registry = fallback_registry(plan, profile.columns)
    # The validator exactly as the application uses it, before anything runs.
    checked = PlanValidator(
        registry, profile, PlanValidationPolicy(max_steps=12, output_targets=frozenset(columns))
    ).validate(plan, instruction=request.question)
    assert checked.valid, checked.errors
    api = api or RowApi(answer, api_key=KEY)
    usage = WebLookupUsage()
    max_rows = 10
    runner = ExcelEnrichmentRunner(
        registry=registry,
        executor=build_live_executor(
            registry,
            max_rows=max_rows,
            web_lookup=WebLookupOptions(api=api, model="m", reasoning_effort=None, usage=usage),
            reuse=reuse,
        ),
        max_rows=max_rows,
        validation_policy=PlanValidationPolicy(max_steps=12),
    )
    output = tmp_path / f"result-{mode}.xlsx"
    result = runner.run(
        source_path=source,
        output_path=output,
        selection=selection,
        plan=plan,
        output_columns=columns,
        dataset_id="d",
        preserve_existing_outputs=True,
        sources_in_workbook=mode,
    )
    return Run(source, output, plan, result, api, usage)


def cells(path: Path) -> dict[str, Any]:
    workbook = load_workbook(path)
    try:
        sheet = workbook[SHEET]
        return {
            c.coordinate: c.value for row in sheet.iter_rows() for c in row if c.value is not None
        }
    finally:
        workbook.close()


def everything_in(path: Path) -> str:
    """Every cell value, note and sheet of a workbook as one text."""
    workbook = load_workbook(path)
    try:
        parts: list[str] = []
        for sheet in workbook:
            for row in sheet.iter_rows():
                for cell in row:
                    parts.append(str(cell.value))
                    if cell.comment is not None:
                        parts.append(cell.comment.text)
        return "\n".join(parts)
    finally:
        workbook.close()


@pytest.mark.parametrize("mode", ["notes", "sheet"])
def test_values_gaps_failures_and_kept_cells(tmp_path: Path, mode: SourcesMode) -> None:
    run = run_fallback(tmp_path, mode)
    before = run.source.read_bytes()
    values = cells(run.output)

    # Everest (row 2) and Lhotse (row 3): number as a number, text as text, yes/no as text.
    for row in (2, 3, 6):
        assert values[f"E{row}"] == 8849 and not isinstance(values[f"E{row}"], str)
        assert values[f"D{row}"] == "1953" and isinstance(values[f"D{row}"], str)
        assert values[f"F{row}"] == "No"
    # K2 (row 4): the height was not given, the rest is written, the filled note is kept.
    assert values["D4"] == "keep me" and values["F4"] == "Yes" and "E4" not in values
    assert run.result.preserved_cells == ("D4",)
    # Nowhere (row 5) and Broken (row 7): nothing written at all.
    assert not any(f"{column}{row}" in values for row in (5, 7) for column in "DEF")
    assert (values["E1"], values["F1"]) == ("Height", "Climbed in winter")

    job = run.result.job_result
    by_row = {r.record_id: r.row_result.steps[0].result for r in job.records}
    assert {key: result.error_code for key, result in by_row.items()} == {
        f"{SHEET}!2": None,
        f"{SHEET}!3": None,
        f"{SHEET}!4": None,
        f"{SHEET}!5": "NOTHING_FOUND",
        f"{SHEET}!6": None,
        f"{SHEET}!7": "INVALID_RESPONSE",
    }
    assert empty_output_fields(run.plan, job) == {f"{SHEET}!4": {"Height": "value_1"}}
    metrics = job.metrics
    assert (metrics.total_rows, metrics.successful_rows, metrics.failed_rows) == (6, 4, 2)

    # The source is never touched.
    assert run.source.read_bytes() == before
    assert run.result.sources_applied


def test_the_requests_follow_the_rows_and_the_usage_counts_them(tmp_path: Path) -> None:
    run = run_fallback(tmp_path, "none")
    asked = [call["input"] for call in run.api.calls]
    # Six rows, one of them a repeat: five requests, counted as made.
    assert len(asked) == 5 <= 6
    assert run.usage.totals().requests == len(run.api.calls) == 5
    assert run.result.job_result.metrics.reused_operations == 1
    assert run.result.job_result.metrics.executed_handler_calls == 5
    assert run.result.job_result.provider_requests["openai_web_search"].operations_started == 5
    lhotse = next(text for text in asked if "Lhotse" in text)
    everest = next(text for text in asked if "Everest" in text)
    assert "- Rank:" not in lhotse and "- Country: Nepal" in lhotse
    assert "- Rank: 1" in everest
    assert all(call["tools"][0]["type"] == "web_search" for call in run.api.calls)


def test_notes_say_found_by_an_ai_and_carry_the_links_and_the_basis(tmp_path: Path) -> None:
    run = run_fallback(tmp_path, "notes")
    workbook = load_workbook(run.output)
    try:
        sheet = workbook[SHEET]
        height = sheet["E2"].comment.text
        assert "Source: OpenAI web search" in height
        assert f"Reference: {PAGE}" in height and f"Kind of trust: {TRUST}" in height
        assert "Basis: stated on the page" in height and "Quote: 8,849 m" in height
        ascent = sheet["D2"].comment.text
        assert f"Reference: {PAGE}; {PAGE_2}" in ascent
        assert "Basis: concluded by the model, not stated on the page" in ascent
        assert f"Reference: {PAGE_2}" in sheet["F4"].comment.text
        # A kept cell, a cell left empty and a failed row have no note.
        assert all(sheet[c].comment is None for c in ("D4", "E4", "D5", "E5", "F5", "D7"))
        assert workbook.sheetnames == [SHEET]
    finally:
        workbook.close()


def test_the_sources_sheet_lists_every_written_cell_with_its_own_links(tmp_path: Path) -> None:
    run = run_fallback(tmp_path, "sheet")
    workbook = load_workbook(run.output)
    try:
        sheet = workbook["Sources"]
        rows: list[list[Any]] = [list(row) for row in sheet.iter_rows(values_only=True)]
        assert tuple(rows[0]) == SHEET_HEADER
        listed: dict[str, list[Any]] = {row[1]: row for row in rows[1:]}
        # Rows 2, 3 and 6: three cells each; K2: the winter cell only (the note is kept).
        assert sorted(listed) == sorted([f"{c}{r}" for r in (2, 3, 6) for c in "DEF"] + ["F4"])
        height = listed["E2"]
        assert tuple(height[:6]) == (SHEET, "E2", "Height", 8849, "web_lookup", "OpenAI web search")
        assert height[6] == f"{PAGE} | Quote: 8,849 m"
        assert height[7] == f"{TRUST} (stated on the page)"
        assert listed["D2"][6].startswith(f"{PAGE}; {PAGE_2}")
        assert listed["D2"][7] == f"{TRUST} (concluded by the model, not stated on the page)"
        assert listed["F4"][6] == PAGE_2
    finally:
        workbook.close()


def test_the_audit_holds_the_evidence_and_neither_the_key_nor_the_raw_reply(
    tmp_path: Path,
) -> None:
    run = run_fallback(tmp_path, "notes")
    job = run.result.job_result
    first = job.records[0].row_result.steps[0].result
    assert [(item.source, item.reference) for item in first.evidence] == [
        ("openai_web_search", PAGE),
        ("openai_web_search", PAGE_2),
    ]
    audit = job.model_dump_json()
    assert PAGE in audit and PAGE_2 in audit
    texts = [
        audit,
        run.plan.model_dump_json(),
        everything_in(run.output),
        json.dumps([r.row_result.model_dump(mode="json") for r in job.records]),
    ]
    for text in texts:
        assert KEY not in text
        assert SENTINEL not in text  # the unreadable reply of the last row is never kept
        assert json.dumps(good_reply()) not in text
    failed = job.records[-1].row_result.steps[0].result
    assert failed.error_code == "INVALID_RESPONSE" and failed.message is not None
    assert all(KEY not in repr(call) for call in run.api.calls)  # the key is not sent as data


def two_target_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = SHEET
    sheet.append(["Mountain", "Notes", "Height"])
    sheet.append(["Everest", "full", 1.0])  # every target filled: nothing to ask
    sheet.append(["Lhotse", "full", None])  # one target empty: asked, and completely
    sheet.append(["K2", None, None])
    workbook.save(path)
    workbook.close()


def test_a_row_with_every_target_filled_is_not_asked_and_a_partly_filled_row_is(
    tmp_path: Path,
) -> None:
    request = FallbackRequest(
        question="Find facts about this mountain.",
        context_columns=("Mountain",),
        targets=(
            FallbackTarget(column="Notes", kind="text"),
            FallbackTarget(column="Height", kind="number", unit="m"),
        ),
    )

    def choose(kwargs: dict[str, Any]) -> Any:
        return response({"value_1": good_reply()["value_2"], "value_2": good_reply()["value_1"]})

    api = RowApi(choose)
    run = run_fallback(
        tmp_path,
        "notes",
        request=request,
        build=two_target_workbook,
        table="A1:C4",
        columns={"Notes": "B", "Height": "C"},
        api=api,
    )
    asked = [call["input"] for call in api.calls]
    assert len(asked) == 2 and not any("Everest" in text for text in asked)
    # The partly filled row is asked for both targets, the kept one included.
    assert "value_1 (Notes)" in asked[0] and "value_2 (Height)" in asked[0]
    assert run.usage.totals().requests == 2
    # The skipped row is reported with the values it holds; no step ran for it.
    assert dict(run.result.skipped_records) == {f"{SHEET}!2": {"B2": "full", "C2": 1.0}}
    assert [r.record_id for r in run.result.job_result.records] == [f"{SHEET}!3", f"{SHEET}!4"]
    values = cells(run.output)
    # The partly filled row was asked for both columns; the filled cell is kept, the empty written.
    assert values["B3"] == "full" and values["C3"] == 8849
    assert values["B4"] == "1953" and values["C4"] == 8849
    assert run.result.preserved_cells == ("B2", "B3", "C2")
    workbook = load_workbook(run.output)
    try:
        sheet = workbook[SHEET]
        assert sheet["B3"].comment is None and sheet["C3"].comment is not None
        assert sheet["B2"].comment is None and sheet["C2"].comment is None
    finally:
        workbook.close()


def sheet_notes(path: Path) -> dict[str, str]:
    """Every note of the first sheet by cell."""
    workbook = load_workbook(path)
    try:
        return {
            cell.coordinate: cell.comment.text
            for row in workbook[SHEET].iter_rows()
            for cell in row
            if cell.comment is not None
        }
    finally:
        workbook.close()


def sources_rows(path: Path) -> list[tuple[Any, ...]]:
    workbook = load_workbook(path)
    try:
        return [tuple(row) for row in workbook["Sources"].iter_rows(values_only=True)]
    finally:
        workbook.close()


@pytest.mark.parametrize("mode", ["notes", "sheet"])
def test_a_second_run_from_kept_answers_writes_the_same_workbook(
    tmp_path: Path, mode: SourcesMode
) -> None:
    caches = tmp_path / "caches"
    caches.mkdir()
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    with (
        SQLiteOperationCache(caches / "values.sqlite") as values,
        SQLiteResolutionCache(caches / "entities.sqlite") as entities,
    ):
        first = run_fallback(tmp_path / "one", mode, reuse=(values, entities))
    assert len(first.api.calls) == 5

    with (
        SQLiteOperationCache(caches / "values.sqlite") as values,
        SQLiteResolutionCache(caches / "entities.sqlite") as entities,
    ):
        # Nowhere ("nothing found") is kept like a found answer; Broken failed to be read
        # and was not kept: it is the only row asked.
        def only_failures(kwargs: dict[str, Any]) -> Any:
            text: str = kwargs["input"]
            assert "- Mountain: Broken" in text, text
            return answer(kwargs)

        second = run_fallback(
            tmp_path / "two",
            mode,
            api=RowApi(only_failures, api_key=KEY),
            reuse=(values, entities),
        )
    assert len(second.api.calls) == second.usage.totals().requests == 1

    # The same cells, the same notes, the same Sources rows: nothing is lost on the way
    # through the file (links, basis, quote, unit, the kept cell).
    assert cells(second.output) == cells(first.output)
    assert sheet_notes(second.output) == sheet_notes(first.output)
    if mode == "sheet":
        assert sources_rows(second.output) == sources_rows(first.output)
    assert second.result.preserved_cells == first.result.preserved_cells == ("D4",)
    assert empty_output_fields(second.plan, second.result.job_result) == empty_output_fields(
        first.plan, first.result.job_result
    )
    before = {record.record_id: record.row_result for record in first.result.job_result.records}
    after = {record.record_id: record.row_result for record in second.result.job_result.records}
    assert after == before  # values, evidence and error codes, row for row
    metrics = second.result.job_result.metrics
    assert (metrics.persistent_cache_hits, metrics.executed_handler_calls) == (4, 1)
    # Five rows are settled by kept answers: Everest twice (one is a repeat), Lhotse, K2
    # and Nowhere (nothing found).
    assert second.result.job_result.kept_answers == {"web_lookup": 5}
    assert second.result.job_result.provider_requests["openai_web_search"].operations_started == 1
