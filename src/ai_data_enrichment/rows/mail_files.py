"""E-mail files as a source of rows: one candidate per message."""

from collections.abc import Sequence

from ai_data_enrichment.mail.messages import MailMessage, sort_messages
from ai_data_enrichment.mail.workbook import mail_cells
from ai_data_enrichment.rows.model import Candidate, RowSource, SourceField

MAIL_FILES = RowSource(
    source_id="mail_files",
    label="E-mail files (.eml)",
    connect="files",
    file_suffixes=(".eml", ".zip"),
    fields=(
        SourceField(
            "received",
            "moment",
            "Received",
            ("received", "date", "datum", "дата", "отримано", "получено"),
        ),
        SourceField(
            "sender",
            "text",
            "Sender",
            (
                "from",
                "sender",
                "von",
                "absender",
                "від",
                "от",
                "отправитель",
                "відправник",
            ),
        ),
        SourceField("subject", "text", "Subject", ("subject", "betreff", "тема")),
    ),
    default_key=("received", "sender"),
    listing="given",
    local_only=True,
)


def mail_candidates(messages: Sequence[MailMessage]) -> list[Candidate]:
    """One candidate per message in the order of ``sort_messages``.

    The values are the ones the mail workbook holds in its Received, From and Subject cells
    (masked). The text of the mail is not part of a candidate.
    """
    ordered = sort_messages(list(messages))
    candidates: list[Candidate] = []
    for message in ordered:
        received, sender, subject = mail_cells(ordered, message, True)
        candidates.append(Candidate({"received": received, "sender": sender, "subject": subject}))
    return candidates
