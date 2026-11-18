"""E-mail messages read from ``.eml`` files."""

import email
import email.policy
import email.utils
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path

from ai_data_enrichment.mail.cleaning import DEFAULT_MAX_CHARS, clean_text

# Tried in this order after the charset the part declares.
_FALLBACK_CHARSETS = ("utf-8", "cp1252")


@dataclass(frozen=True)
class MailMessage:
    """One mail: header facts and the cleaned text of the body (see ``mail.cleaning``)."""

    message_id: str
    received: datetime | None
    sender_name: str
    sender_address: str
    recipient_address: str
    subject: str
    text: str
    truncated: bool
    source: str


def _decode(payload: bytes, declared: str | None) -> str:
    for charset in (declared, *_FALLBACK_CHARSETS):
        if not charset:
            continue
        try:
            return payload.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _body_parts(message: Message) -> tuple[str, str]:
    """The text of every inline text/plain and text/html part; attachments are skipped."""
    plain: list[str] = []
    html: list[str] = []
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        (plain if kind == "text/plain" else html).append(
            _decode(payload, part.get_content_charset())
        )
    return "\n".join(plain), "\n".join(html)


def _header(message: Message, name: str) -> str:
    try:
        return " ".join(str(message.get(name, "")).split())
    except Exception:  # noqa: BLE001 - a header that cannot be decoded is read as empty
        return ""


def _first_address(value: str) -> str:
    return email.utils.parseaddr(value)[1].strip().lower()


def _received(message: Message) -> datetime | None:
    raw = _header(message, "Date")
    if not raw:
        return None
    try:
        moment = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def parse_message(raw: bytes, *, source: str, max_chars: int = DEFAULT_MAX_CHARS) -> MailMessage:
    """Read one RFC 822 message from bytes; raises ``ValueError`` when it is not a mail.

    The one parser behind every source of messages: ``.eml`` files and Gmail. ``source`` is
    the name the message goes by in the output (a file name, for instance).
    """
    try:
        message = email.message_from_bytes(raw, policy=email.policy.default)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{type(exc).__name__}") from exc
    if not message.keys():
        raise ValueError("no headers")
    plain, html = _body_parts(message)
    text, truncated = clean_text(plain, html, max_chars=max_chars)
    name, address = email.utils.parseaddr(_header(message, "From"))
    recipient = _first_address(_header(message, "To")) or _first_address(
        _header(message, "Delivered-To")
    )
    return MailMessage(
        message_id=_header(message, "Message-ID").strip("<> "),
        received=_received(message),
        sender_name=" ".join(name.split()),
        sender_address=address.strip().lower(),
        recipient_address=recipient,
        subject=_header(message, "Subject"),
        text=text,
        truncated=truncated,
        source=source,
    )


def read_eml_file(path: Path, *, max_chars: int = DEFAULT_MAX_CHARS) -> MailMessage:
    """Read one file; raises ``ValueError`` when it is not a readable mail."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{type(exc).__name__}") from exc
    return parse_message(raw, source=path.name, max_chars=max_chars)


def sort_messages(messages: list[MailMessage]) -> list[MailMessage]:
    """By the ``Date`` header, then by source name; a mail without a date comes last."""
    return sorted(
        messages,
        key=lambda m: (
            m.received is None,
            m.received or datetime.min.replace(tzinfo=UTC),
            m.source,
        ),
    )


def read_eml_folder_report(
    path: Path, *, max_chars: int = DEFAULT_MAX_CHARS
) -> tuple[list[MailMessage], list[str]]:
    """Every ``*.eml`` in the folder (not its subfolders), and the names of files skipped.

    Sorted by the ``Date`` header, then by file name; a mail without a date comes last.
    A file that cannot be read is skipped, never fatal.
    """
    messages: list[MailMessage] = []
    skipped: list[str] = []
    for file in sorted(path.glob("*.eml")):
        if not file.is_file():
            continue
        try:
            messages.append(read_eml_file(file, max_chars=max_chars))
        except ValueError:
            skipped.append(file.name)
    return sort_messages(messages), skipped


def read_eml_folder(path: Path, *, max_chars: int = DEFAULT_MAX_CHARS) -> list[MailMessage]:
    """The messages of ``read_eml_folder_report`` without the list of skipped files."""
    return read_eml_folder_report(path, max_chars=max_chars)[0]
