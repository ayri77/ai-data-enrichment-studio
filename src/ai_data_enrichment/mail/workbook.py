"""The workbook an application table of e-mails is made of: one row per message.

Four columns, ``Text`` last. ``Text`` is exactly the masked document of
``mail.documents.prepare``, so that a later check of a quote runs against the very text of
the cell. Every text cell is a string cell, whatever it starts with.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ai_data_enrichment.mail.documents import own_addresses, prepare
from ai_data_enrichment.mail.masking import KINDS, mask_text
from ai_data_enrichment.mail.messages import MailMessage, sort_messages

SHEET_NAME = "Mail"
HEADERS = ("Received", "From", "Subject", "Text")
RECEIVED_FORMAT = "yyyy-mm-dd hh:mm"
# Excel holds at most 32,767 characters in a cell.
MAX_CELL_CHARS = 32_767
_WIDTHS = (18, 28, 48, 40)
# Excel has no dates before 1900.
_EARLIEST = datetime(1900, 1, 1)


@dataclass(frozen=True)
class MailImportSummary:
    """What an import did, as counts only: no name, address or text."""

    read: int
    skipped: int = 0
    truncated: int = 0
    masked: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))


def sender_label(message: MailMessage) -> str:
    """The sender's name, or the domain of the address when there is no name."""
    if message.sender_name:
        return message.sender_name
    return message.sender_address.rpartition("@")[2] or message.sender_address


def _received(message: MailMessage) -> datetime | None:
    """The time of the message in UTC, without a time zone; None when it cannot be one."""
    if message.received is None:
        return None
    try:
        moment = message.received.astimezone(UTC).replace(tzinfo=None, second=0, microsecond=0)
    except (OverflowError, ValueError, OSError):
        return None
    return moment if moment >= _EARLIEST else None


def cell_text(value: str) -> str:
    """The text as a cell can hold it: illegal characters removed, cut to ``MAX_CELL_CHARS``."""
    return ILLEGAL_CHARACTERS_RE.sub("", value)[:MAX_CELL_CHARS]


def mail_cells(
    ordered: Sequence[MailMessage], message: MailMessage, mask: bool
) -> tuple[datetime | None, str, str]:
    """The Received, From and Subject values of a message, as the workbook holds them.

    ``ordered`` is the list the message belongs to (it decides which address is the
    mailbox owner's). The text is masked when ``mask`` is set and cleaned for a cell.
    """
    subject, sender = " ".join(message.subject.split()), sender_label(message)
    if mask:
        own = own_addresses(ordered, message)
        subject = mask_text(subject, own_addresses=own)[0]
        sender = mask_text(sender, own_addresses=own)[0]
    return _received(message), cell_text(sender), cell_text(subject)


def _write_text(sheet: Worksheet, row: int, column: int, value: str) -> None:
    """A string cell: text that starts with = + - @ stays text and is never a formula."""
    text = cell_text(value)
    if not text:
        return
    sheet.cell(row=row, column=column, value=text).data_type = "s"


def write_mail_workbook(
    messages: Sequence[MailMessage], path: Path, *, mask: bool = True
) -> MailImportSummary:
    """Write one sheet, ``Mail``, with one row per message in the order of ``sort_messages``."""
    ordered = sort_messages(list(messages))
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = SHEET_NAME
    for index, header in enumerate(HEADERS, start=1):
        sheet.cell(row=1, column=index, value=header).font = Font(bold=True)
        sheet.column_dimensions[get_column_letter(index)].width = _WIDTHS[index - 1]
    sheet.freeze_panes = "A2"
    masked: dict[str, int] = dict.fromkeys(KINDS, 0)
    for row, message in enumerate(ordered, start=2):
        document, counts = prepare(ordered, message, mask)
        for kind in KINDS:
            masked[kind] += counts[kind]
        moment, sender, subject = mail_cells(ordered, message, mask)
        if moment is not None:
            sheet.cell(row=row, column=1, value=moment).number_format = RECEIVED_FORMAT
        _write_text(sheet, row, 2, sender)
        _write_text(sheet, row, 3, subject)
        _write_text(sheet, row, 4, document)
    workbook.save(path)
    return MailImportSummary(
        read=len(ordered),
        truncated=sum(1 for message in ordered if message.truncated),
        masked=masked,
    )
