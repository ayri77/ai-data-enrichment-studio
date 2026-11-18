"""Reading e-mails from files, cleaning their text and masking it before it is sent anywhere.

This package belongs to the mail extraction probe (docs/32-mail-extraction-probe.md). It
reads ``.eml`` files and, with the optional ``mail`` extra, the person's own Gmail
mailbox read-only (``mail.gmail``); both go through ``parse_message``.
"""

from ai_data_enrichment.mail.cleaning import clean_text
from ai_data_enrichment.mail.masking import mask_text
from ai_data_enrichment.mail.messages import (
    MailMessage,
    parse_message,
    read_eml_folder,
    read_eml_folder_report,
)

__all__ = [
    "MailMessage",
    "clean_text",
    "mask_text",
    "parse_message",
    "read_eml_folder",
    "read_eml_folder_report",
]
