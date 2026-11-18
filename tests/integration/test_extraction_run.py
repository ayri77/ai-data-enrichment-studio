"""The text extraction from a table of mails to a result workbook, offline.

The plan is built for the table the application makes of the invented fixture mails, validated
by the real validator with the registry made for that plan, and run by ``ExcelEnrichmentRunner``
with the real handler, reply reader and workbook writer. Only the API object is fake.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook
from tests.support.mail_table import SELECTION, mail_table
from tests.support.text_extraction import answer, entry, response
from tests.support.web_lookup import RowApi

from ai_data_enrichment.capabilities.extraction import (
    ExtractionRequest,
    ExtractionTarget,
    build_extraction_plan,
    extraction_registry,
)
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.models import FoundRecord
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.orchestration.excel_runner import (
    ExcelEnrichmentResult,
    ExcelEnrichmentRunner,
)
from ai_data_enrichment.orchestration.live_executor import ExtractionOptions, build_live_executor
from ai_data_enrichment.output.provenance import SHEET_HEADER, SourcesMode
from ai_data_enrichment.providers.text_extraction import ExtractionUsage
from ai_data_enrichment.resolution.sqlite_cache import SQLiteResolutionCache
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy, PlanValidator

SHEET = "Mail"
SOURCE = "openai_text_extraction"
REQUEST = ExtractionRequest(
    question="Payments and subscriptions",
    text_column="Text",
    targets=(
        ExtractionTarget(column="Service", kind="text"),
        ExtractionTarget(column="Amount", kind="number"),
        ExtractionTarget(column="Paid on", kind="date"),
        ExtractionTarget(column="Auto renews", kind="yes_no"),
        ExtractionTarget(column="Event", kind="one_of", options=("payment", "ended")),
    ),
)
OUTPUT_COLUMNS = {"Service": "E", "Amount": "F", "Paid on": "G", "Auto renews": "H", "Event": "I"}

# Rows of the table (the order of ``sort_messages``): 2 Streamly (uk, ended), 3 Cloudbox (de),
# 4 PayRelay for AppMarket, 5 PayRelay for HostHill, 6 HostHill price change, 7 AppMarket
# Black Friday, 8 Streamly (ru), 9 Streamly newsletter, 10 Streamly trial ends, 11 HostHill
# payment failed.
NOT_APPLICABLE_ROWS = (7, 9)
NOTHING_FOUND_ROWS = (2, 10, 11)
FOUND_ROWS = (3, 4, 5, 6, 8)


def say(**slots: tuple[Any, str]) -> dict[str, Any]:
    values = {f"value_{i}": entry() for i in range(1, 6)}
    values.update({name: entry(*item) for name, item in slots.items()})
    return {"applies": True, "values": values}


# What the fake model says, by a passage only that mail has.
REPLIES: dict[str, dict[str, Any]] = {
    "Cloudbox Plus (2 TB Speicher)": say(
        value_1=("Cloudbox Plus", "Tarif: Cloudbox Plus (2 TB Speicher)"),
        value_2=(10.0, "Gesamt: 10,00 EUR"),
        value_3=("2026-11-01", "Bezahlt am 01.11.2026"),
        value_4=("Yes", "Ihr Tarif verlängert sich automatisch am 01.12.2026."),
        value_5=("payment", "vielen Dank für Ihre Zahlung."),
    ),
    "Notes App Pro (annual plan)": say(
        value_1=("Notes App Pro", "Notes App Pro (annual plan)"),
        value_2=(29.99, "$29.99"),
        value_3=("2026-11-03", "3 Nov 2026"),
    ),
    "HostHill Starter, monthly": say(
        value_1=("HostHill Starter", "Item: HostHill Starter, monthly"),
        value_2=(4.5, "Amount: 4.50 EUR"),
        value_3=("2026-11-04", "Date: 2026-11-04"),
    ),
    "HostHill Dedicated changes from": say(
        value_1=("HostHill Dedicated", "the price of HostHill Dedicated changes from"),
        value_2=(1299.0, "1,299.00 USD"),
    ),
    "Сервис: Streamly Premium": say(
        value_1=("Streamly Premium", "Сервис: Streamly Premium"),
        value_2=(399.0, "Стоимость: 399,00 ₽ в месяц"),
        value_3=("2026-11-12", "Списано: 12 ноября 2026"),
        value_4=("Yes", "Следующее списание: 12 декабря 2026"),
        value_5=("payment", "Спасибо за оплату."),
    ),
    "Black Friday at AppMarket": answer(applies=False),
    "New this week on Streamly": answer(applies=False),
}


def choose(kwargs: dict[str, Any]) -> Any:
    text: str = kwargs["input"]
    for passage, reply in REPLIES.items():
        if passage in text:
            return response(reply)
    return response(answer())  # the text is about the task, but says none of the values


@dataclass
class Run:
    output: Path
    plan: EnrichmentPlan
    result: ExcelEnrichmentResult
    api: RowApi
    usage: ExtractionUsage


def run_extraction(
    tmp_path: Path,
    mode: SourcesMode = "none",
    *,
    api: RowApi | None = None,
    reuse: tuple[SQLiteOperationCache, SQLiteResolutionCache] | None = None,
) -> Run:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "mail.xlsx"
    columns = mail_table(source)
    plan = build_extraction_plan(REQUEST, columns)
    registry = extraction_registry(plan, columns)
    profile = profile_worksheet(source, SHEET, "mail", selection=SELECTION)
    # The validator exactly as the application uses it, before anything runs.
    checked = PlanValidator(
        registry,
        profile,
        PlanValidationPolicy(max_steps=12, output_targets=frozenset(OUTPUT_COLUMNS)),
    ).validate(plan, instruction=REQUEST.question)
    assert checked.valid, checked.errors
    api = api or RowApi(choose)
    usage = ExtractionUsage()
    runner = ExcelEnrichmentRunner(
        registry=registry,
        executor=build_live_executor(
            registry,
            max_rows=20,
            text_extraction=ExtractionOptions(
                api=api, model="m", reasoning_effort=None, usage=usage
            ),
            reuse=reuse,
        ),
        max_rows=20,
        validation_policy=PlanValidationPolicy(max_steps=12),
    )
    output = tmp_path / f"result-{mode}.xlsx"
    result = runner.run(
        source_path=source,
        output_path=output,
        selection=SELECTION,
        plan=plan,
        output_columns=OUTPUT_COLUMNS,
        dataset_id="mail",
        preserve_existing_outputs=True,
        sources_in_workbook=mode,
    )
    return Run(output, plan, result, api, usage)


def cells(path: Path) -> dict[str, Any]:
    workbook = load_workbook(path)
    try:
        sheet = workbook[SHEET]
        return {
            c.coordinate: c.value for row in sheet.iter_rows() for c in row if c.value is not None
        }
    finally:
        workbook.close()


def codes(run: Run) -> dict[int, str | None]:
    return {
        int(record.record_id.split("!")[1]): record.row_result.steps[0].result.error_code
        for record in run.result.job_result.records
    }


def test_values_of_every_kind_arrive_typed_and_the_other_rows_fail_with_their_codes(
    tmp_path: Path,
) -> None:
    run = run_extraction(tmp_path)
    values = cells(run.output)

    assert (values["E1"], values["F1"], values["G1"]) == ("Service", "Amount", "Paid on")
    # Text as text, a number as a number, a date as YYYY-MM-DD text, yes/no and a choice as text.
    assert values["E3"] == "Cloudbox Plus" and isinstance(values["E3"], str)
    assert values["F3"] == 10 and not isinstance(values["F3"], str)
    assert values["G3"] == "2026-11-01" and isinstance(values["G3"], str)
    assert values["H3"] == "Yes" and values["I3"] == "payment"
    assert values["F4"] == 29.99 and values["G4"] == "2026-11-03"
    assert values["F5"] == 4.5 and values["F6"] == 1299
    assert values["G8"] == "2026-11-12" and values["H8"] == "Yes"
    # A slot the text did not say is left empty; the row is still a success.
    assert not any(f"{c}4" in values for c in "HI") and "G6" not in values

    job = run.result.job_result
    expected: dict[int, str | None] = dict.fromkeys(FOUND_ROWS)
    expected.update(dict.fromkeys(NOT_APPLICABLE_ROWS, "NOT_APPLICABLE"))
    expected.update(dict.fromkeys(NOTHING_FOUND_ROWS, "NOTHING_FOUND"))
    assert codes(run) == dict(sorted(expected.items()))
    failed = {r.record_id: r.row_result.steps[0].result for r in job.records}
    for row in NOT_APPLICABLE_ROWS:
        result = failed[f"{SHEET}!{row}"]
        assert result.status == "failure" and result.value is None
        assert result.message == "The text is not about the task."
        assert not any(f"{c}{row}" in values for c in "EFGHI")  # nothing is written
    metrics = job.metrics
    assert (metrics.total_rows, metrics.successful_rows, metrics.failed_rows) == (10, 5, 5)

    # The values in the audit are as typed as in the cells.
    record = job.records[1].row_result.steps[0].result.value  # row 3
    assert isinstance(record, FoundRecord)
    assert type(record.slots["value_2"].value) is float
    assert record.slots["value_3"].value == "2026-11-01"
    assert [record.slots[s].basis for s in ("value_1", "value_2", "value_3", "value_4")] == [
        "literal",
        "literal",
        "literal",
        "derived",
    ]


def test_one_request_per_row_with_the_text_of_the_row_and_nothing_else_of_the_table(
    tmp_path: Path,
) -> None:
    run = run_extraction(tmp_path)
    assert len(run.api.calls) == run.usage.totals().requests == 10
    assert run.result.job_result.provider_requests[SOURCE].operations_started == 10
    asked = [call["input"] for call in run.api.calls]
    assert any("Gesamt: 10,00 EUR" in text for text in asked)
    assert all("tools" not in call for call in run.api.calls)  # no web search


@pytest.mark.parametrize("mode", ["notes", "sheet"])
def test_the_workbook_says_where_each_value_came_from_with_the_quote(
    tmp_path: Path, mode: SourcesMode
) -> None:
    run = run_extraction(tmp_path, mode)
    workbook = load_workbook(run.output)
    try:
        sheet = workbook[SHEET]
        if mode == "notes":
            note = sheet["F3"].comment.text
            assert "Source: OpenAI text extraction" in note
            assert "Basis: stated in the quoted passage" in note and "Reference" not in note
            assert "Quote: Gesamt: 10,00 EUR" in note and "Kind of trust: read by an AI" in note
            assert "Basis: derived from the quoted passage" in sheet["H3"].comment.text
            assert all(sheet[f"{c}7"].comment is None for c in "EFGHI")
        else:
            rows: list[list[Any]] = [
                list(r) for r in workbook["Sources"].iter_rows(values_only=True)
            ]
            assert tuple(rows[0]) == SHEET_HEADER
            listed = {row[1]: row for row in rows[1:]}
            assert listed["F3"][4:6] == ["text_extraction", "OpenAI text extraction"]
            assert listed["F3"][6] == "Quote: Gesamt: 10,00 EUR"
            assert (
                "extracted" not in listed["F3"][7]
                and "stated in the quoted passage" in (listed["F3"][7])
            )
    finally:
        workbook.close()


def test_a_second_run_asks_again_only_the_texts_that_are_not_about_the_task(
    tmp_path: Path,
) -> None:
    caches = tmp_path / "caches"
    caches.mkdir()
    with (
        SQLiteOperationCache(caches / "values.sqlite") as values,
        SQLiteResolutionCache(caches / "entities.sqlite") as entities,
    ):
        first = run_extraction(tmp_path / "one", reuse=(values, entities))
    assert len(first.api.calls) == 10

    with (
        SQLiteOperationCache(caches / "values.sqlite") as values,
        SQLiteResolutionCache(caches / "entities.sqlite") as entities,
    ):
        # Found values, and "the text is about the task but says nothing", are kept like an
        # answer; "the text is not about the task" is a failure and is asked again.
        def only_not_applicable(kwargs: dict[str, Any]) -> Any:
            text: str = kwargs["input"]
            assert "Black Friday at AppMarket" in text or "New this week on Streamly" in text
            return choose(kwargs)

        second = run_extraction(
            tmp_path / "two", api=RowApi(only_not_applicable), reuse=(values, entities)
        )
    assert len(second.api.calls) == second.usage.totals().requests == 2

    assert cells(second.output) == cells(first.output)
    assert codes(second) == codes(first)
    before = {r.record_id: r.row_result for r in first.result.job_result.records}
    after = {r.record_id: r.row_result for r in second.result.job_result.records}
    assert after == before
    metrics = second.result.job_result.metrics
    assert (metrics.persistent_cache_hits, metrics.executed_handler_calls) == (8, 2)
    assert second.result.job_result.kept_answers == {"text_extraction": 8}
    assert second.result.job_result.provider_requests[SOURCE].operations_started == 2


def test_what_the_cache_holds_on_disk_for_this_capability(tmp_path: Path) -> None:
    caches = tmp_path / "caches"
    caches.mkdir()
    with (
        SQLiteOperationCache(caches / "values.sqlite") as values,
        SQLiteResolutionCache(caches / "entities.sqlite") as entities,
    ):
        run_extraction(tmp_path / "one", reuse=(values, entities))
    connection = sqlite3.connect(caches / "values.sqlite")
    try:
        rows = connection.execute(
            "SELECT key_digest, result_json, capability_id, provider_id FROM operation_cache"
        ).fetchall()
    finally:
        connection.close()
    # Eight answers: five found, three "nothing found"; the two not-applicable texts are absent.
    assert len(rows) == 8 and {(r[2], r[3]) for r in rows} == {("text_extraction", SOURCE)}
    stored = "\n".join(" ".join(map(str, row)) for row in rows)
    # The mail text is not stored, nor is the task: the key is a SHA-256 digest of both.
    assert all(len(r[0]) == 64 for r in rows)
    for text in ("Tarif: Cloudbox Plus (2 TB Speicher)\nZeitraum", "Payments and subscriptions"):
        assert text not in stored
    assert "Sam" not in stored and "Hallo" not in stored
    # The extracted values and their quotes are stored in clear.
    assert "Gesamt: 10,00 EUR" in stored and "Cloudbox Plus" in stored
