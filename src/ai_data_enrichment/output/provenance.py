"""Say in the result workbook where each written value came from.

The run writes values into a copy of the workbook; the audit says where each
came from. With ``sources_in_workbook`` the copy says it too, in one of two
ways: a cell note on every cell the run wrote, or one added worksheet listing
them. Only cells this run wrote are described. The texts are English and are not
localized: a workbook is a file that is passed on, not a page in the interface.
"""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkstemp
from typing import Literal

from openpyxl import load_workbook
from openpyxl.comments import Comment

from ai_data_enrichment.capabilities.bindings import Trust, find_binding
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.job_models import JobExecutionResult
from ai_data_enrichment.execution.models import FoundRecord

SourcesMode = Literal["none", "notes", "sheet"]

SOURCES_SHEET = "Sources"
NOTE_AUTHOR = "AI Data Enrichment Studio"
SHEET_HEADER = (
    "Sheet",
    "Cell",
    "Column",
    "Value",
    "Capability",
    "Source",
    "Reference",
    "Kind of trust",
)

# The kind of trust in plain words, as the workbook says it.
TRUST_WORDS: Mapping[str, str] = {
    Trust.COMPUTED: "calculated here from other values of the row",
    Trust.RECORD: "read from a record of the source",
    Trust.RESOLVED_NAME: "a name matched to a record of the source",
    Trust.SERVICE: "calculated by an outside service; there is no record to check it against",
    Trust.AI_FOUND: "found by an AI web search, not verified",
}
# What a source is called to a reader; an id without a name is shown as it is.
SOURCE_NAMES: Mapping[str, str] = {
    "local": "calculated here",
    "open-meteo": "Open-Meteo",
    "wikidata": "Wikidata",
    "openrouteservice": "openrouteservice (OpenStreetMap data)",
    "openai_web_search": "OpenAI web search",
}
# How a found value rests on its page, in plain words.
BASIS_WORDS: Mapping[str, str] = {
    "stated": "stated on the page",
    "concluded": "concluded by the model, not stated on the page",
}


@dataclass(frozen=True, slots=True)
class CellProvenance:
    """Where the value of one written cell came from."""

    cell: str
    column: str
    value: str | float
    capability: str
    source: str
    reference: str
    trust: str
    # Only for a value found by an AI web search: "stated" or "concluded", and
    # the passage quoted from the page. Empty for every other kind of cell.
    basis: str = ""
    quote: str = ""


def source_name(source_id: str) -> str:
    return SOURCE_NAMES.get(source_id, source_id)


def trust_words(trust: str) -> str:
    return TRUST_WORDS.get(trust, trust)


def build_cell_provenance(
    plan: EnrichmentPlan,
    job_result: JobExecutionResult,
    *,
    row_by_record_id: Mapping[str, int],
    output_columns: Mapping[str, str],
    written: Mapping[str, str | float],
) -> list[CellProvenance]:
    """One entry per cell in ``written``, in sheet order (row, then plan output)."""
    capability_of = {step.id: step.capability for step in plan.steps}
    items: list[tuple[int, int, CellProvenance]] = []
    for record in job_result.records:
        row = row_by_record_id.get(record.record_id)
        if row is None:
            continue
        results = {step.step_id: step.result for step in record.row_result.steps}
        for position, output in enumerate(plan.outputs):
            letter = output_columns[output.target_column]
            cell = f"{letter}{row}"
            if cell not in written or output.step_id not in results:
                continue
            capability = capability_of[output.step_id]
            binding = find_binding(capability)
            result = results[output.step_id]
            references = [item.reference for item in result.evidence]
            basis, quote = "", ""
            if isinstance(result.value, FoundRecord):
                # A found value rests on its own links, not on the step's evidence.
                slot = result.value.slots.get(output.value_field)
                if slot is None or slot.value is None:
                    continue
                references = list(slot.sources)
                basis, quote = slot.basis or "", slot.quote or ""
            items.append(
                (
                    row,
                    position,
                    CellProvenance(
                        cell=cell,
                        column=output.target_column,
                        value=written[cell],
                        capability=capability,
                        source=binding.source_id if binding else "",
                        reference="; ".join(references),
                        trust=binding.trust.value if binding else "",
                        basis=basis,
                        quote=quote,
                    ),
                )
            )
    return [item for _, _, item in sorted(items, key=lambda entry: entry[:2])]


def note_text(entry: CellProvenance) -> str:
    lines = [f"Source: {source_name(entry.source) if entry.source else 'unknown'}"]
    if entry.reference:
        lines.append(f"Reference: {entry.reference}")
    lines.append(f"Kind of trust: {trust_words(entry.trust) if entry.trust else 'unknown'}")
    if entry.basis:
        lines.append(f"Basis: {BASIS_WORDS.get(entry.basis, entry.basis)}")
    if entry.quote:
        lines.append(f"Quote: {entry.quote}")
    return "\n".join(lines)


def sheet_trust(entry: CellProvenance) -> str:
    """The "Kind of trust" cell; a found value carries its basis here."""
    words = trust_words(entry.trust)
    if entry.basis:
        return f"{words} ({BASIS_WORDS.get(entry.basis, entry.basis)})"
    return words


def sheet_reference(entry: CellProvenance) -> str:
    """The "Reference" cell; a found value's quote follows its links."""
    if entry.quote:
        return f"{entry.reference} | Quote: {entry.quote}" if entry.reference else entry.quote
    return entry.reference


def free_sheet_name(existing: Sequence[str]) -> str:
    """``Sources``, or ``Sources (2)``, ``Sources (3)`` ... where the name is taken."""
    taken = {name.casefold() for name in existing}
    name, number = SOURCES_SHEET, 1
    while name.casefold() in taken:
        number += 1
        name = f"{SOURCES_SHEET} ({number})"
    return name


def annotate_workbook(
    path: Path,
    *,
    sheet_name: str,
    mode: SourcesMode,
    entries: Sequence[CellProvenance],
) -> None:
    """Add the notes or the sheet to the workbook at ``path``, in place.

    The workbook is saved to a temporary file and moved over ``path`` only once
    it has been written and opened again, so a failure leaves the workbook as it
    was. The caller decides what a failure means.
    """
    if mode == "none" or not entries:
        return
    workbook = load_workbook(path, read_only=False, data_only=False)
    try:
        if mode == "notes":
            sheet = workbook[sheet_name]
            for entry in entries:
                comment = Comment(note_text(entry), NOTE_AUTHOR)
                comment.width, comment.height = 320, 90 if not entry.basis else 150
                # An earlier note on a written cell gives way to this one.
                sheet[entry.cell].comment = comment
        else:
            added = workbook.create_sheet(free_sheet_name(workbook.sheetnames))
            added.append(list(SHEET_HEADER))
            for entry in entries:
                added.append(
                    [
                        sheet_name,
                        entry.cell,
                        entry.column,
                        entry.value,
                        entry.capability,
                        source_name(entry.source),
                        sheet_reference(entry),
                        sheet_trust(entry),
                    ]
                )
            # Text that starts with "=" stays text.
            for row in added.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
        fd, name = mkstemp(prefix=f".{path.stem}-", suffix=".xlsx", dir=path.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            workbook.save(temporary)
            load_workbook(temporary, read_only=True, data_only=False).close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        workbook.close()
