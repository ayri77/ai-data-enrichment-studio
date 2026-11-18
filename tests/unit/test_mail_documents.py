"""The document of a message and the own-address rule, shared by the probe and the application."""

from datetime import UTC, datetime

from ai_data_enrichment.mail.documents import message_document, own_addresses, prepare
from ai_data_enrichment.mail.messages import MailMessage


def mail(
    *, recipient: str = "me@example.org", text: str = "Body", subject: str = "S", name: str = ""
) -> MailMessage:
    return MailMessage(
        message_id="x",
        received=datetime(2026, 11, 1, 23, 30, tzinfo=UTC),
        sender_name=name,
        sender_address="billing@example.com",
        recipient_address=recipient,
        subject=subject,
        text=text,
        truncated=False,
        source="x.eml",
    )


def test_the_document_has_subject_sender_and_date_then_the_body() -> None:
    assert message_document(mail(subject="A  B", name="Cloudbox")) == (
        "Subject: A B\nFrom: Cloudbox <billing@example.com>\nDate: 2026-11-01\n\nBody"
    )
    undated = MailMessage("", None, "", "", "", "S", "Body", False, "u.eml")
    assert message_document(undated) == "Subject: S\nFrom: \n\nBody"


def test_own_addresses_are_the_recipient_the_commonest_one_and_the_known_ones() -> None:
    batch = [
        mail(recipient="a@example.org"),
        mail(recipient="a@example.org"),
        mail(recipient="b@x"),
    ]
    assert own_addresses(batch, batch[2]) == {"a@example.org", "b@x"}
    assert own_addresses(batch, batch[0], ["c@x", ""]) == {"a@example.org", "c@x"}
    assert own_addresses([], mail(recipient="")) == set()


def test_prepare_masks_the_document_and_counts() -> None:
    message = mail(text="Sent to me@example.org, call +1 555 010 0199, card ending in 1234.")
    document, counts = prepare([message], message, True)
    assert "me@example.org" not in document and "[me]" in document
    assert "[phone]" in document and "[card]" in document
    assert counts == {"card": 1, "phone": 1, "me": 1}


def test_prepare_without_masking_changes_nothing() -> None:
    message = mail(text="Sent to me@example.org")
    document, counts = prepare([message], message, False)
    assert document == message_document(message)
    assert counts == {"card": 0, "phone": 0, "me": 0}
