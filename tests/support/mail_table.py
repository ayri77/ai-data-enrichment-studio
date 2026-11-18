"""The table of the invented fixture mails (``samples/probe/mail``), as the application makes it."""

from pathlib import Path

from ai_data_enrichment.domain.dataset_profile import ColumnProfile
from ai_data_enrichment.ingestion.excel_profiler import profile_worksheet
from ai_data_enrichment.ingestion.table_selection import TableSelection
from ai_data_enrichment.mail.messages import read_eml_folder
from ai_data_enrichment.mail.workbook import SHEET_NAME, write_mail_workbook

FIXTURES = Path(__file__).parents[2] / "samples" / "probe" / "mail"
SELECTION = TableSelection(SHEET_NAME, "A1:D11")


def mail_table(path: Path) -> list[ColumnProfile]:
    """Write the workbook of the ten fixture mails to ``path``; return its profiled columns."""
    write_mail_workbook(read_eml_folder(FIXTURES), path)
    return profile_worksheet(path, SHEET_NAME, "mail", selection=SELECTION).columns
