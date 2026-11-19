"""The text of a message as it is read by a model and written to a table.

Shared by the mail probe (``scripts/experiment_mail_extraction.py``) and the table built
from e-mails in the application: one document per message, with the same masking, so that
what is checked later is checked against exactly what was shown.
"""

from collections import Counter
from collections.abc import Collection, Sequence

from ai_data_enrichment.mail.masking import KINDS as MASK_KINDS
from ai_data_enrichment.mail.masking import mask_text
from ai_data_enrichment.mail.messages import MailMessage


def message_document(message: MailMessage) -> str:
    """The text of one message as the model reads it: subject, sender, date, then the body."""
    sender = message.sender_name or ""
    if message.sender_address:
        sender = f"{sender} <{message.sender_address}>".strip()
    head = [f"Subject: {' '.join(message.subject.split())}", f"From: {sender}"]
    if message.received is not None:
        head.append(f"Date: {message.received.date().isoformat()}")
    return "\n".join(head) + "\n\n" + message.text


def own_addresses(
    messages: Sequence[MailMessage], message: MailMessage, known: Collection[str] = ()
) -> set[str]:
    """The mailbox owner's addresses: this message's recipient, the commonest one, and the
    address the mailbox itself reports (Gmail)."""
    counts = Counter(m.recipient_address for m in messages if m.recipient_address)
    common = {address for address, _ in counts.most_common(1)}
    return {a for a in (message.recipient_address, *common, *known) if a}


def prepare(
    messages: Sequence[MailMessage],
    message: MailMessage,
    mask: bool,
    known: Collection[str] = (),
) -> tuple[str, dict[str, int]]:
    """The document of one message, masked unless ``mask`` is off, and the masking counts."""
    document = message_document(message)
    if not mask:
        return document, dict.fromkeys(MASK_KINDS, 0)
    return mask_text(document, own_addresses=own_addresses(messages, message, known))
