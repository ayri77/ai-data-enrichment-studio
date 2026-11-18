"""Read an uploaded ``.zip`` of ``.eml`` files, or one ``.eml``, from memory.

Nothing is extracted to disk and no entry name is used as a path. Sizes are checked as
declared before an entry is read and again while it is read, so that an archive that
lies about its sizes cannot expand past the bounds. Errors are fixed sentences: a broken
entry is counted, never named.
"""

import zlib
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from ai_data_enrichment.mail.messages import MailMessage, parse_message
from ai_data_enrichment.workspace_errors import WorkspaceError

MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 500
MAX_ENTRY_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def _entry_too_large() -> WorkspaceError:
    return WorkspaceError(
        "mail_entry_too_large", "A mail in the upload is larger than 10 MiB.", 413
    )


def _total_too_large() -> WorkspaceError:
    return WorkspaceError(
        "mail_total_too_large", "The mails in the upload add up to more than 64 MiB.", 413
    )


_READ_ERRORS = (BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError, OSError)


def _parse(raw: bytes, source: str) -> MailMessage | None:
    try:
        return parse_message(raw, source=source)
    except Exception:  # noqa: BLE001 - whatever is wrong with one mail, it is only counted
        return None


def read_mail_upload(data: bytes, *, archive: bool) -> tuple[list[MailMessage], int]:
    """The readable messages of the upload and the number of entries or files skipped.

    ``archive`` says whether the upload is a ``.zip`` (else one ``.eml``). Raises
    ``WorkspaceError`` with a fixed message when a bound is exceeded or no mail can be read.
    """
    messages: list[MailMessage] = []
    skipped = 0
    if not archive:
        if len(data) > MAX_ENTRY_BYTES:
            raise _entry_too_large()
        message = _parse(data, "0")
        if message is not None:
            messages.append(message)
        skipped = 0 if message is not None else 1
    else:
        try:
            zipped = ZipFile(BytesIO(data))
        except _READ_ERRORS:
            raise WorkspaceError("invalid_mail_archive", "Cannot read this .zip file.") from None
        with zipped:
            entries = [e for e in zipped.infolist() if not e.is_dir()]
            mails = [e for e in entries if e.filename.lower().endswith(".eml")]
            skipped = len(entries) - len(mails)
            if len(mails) > MAX_ENTRIES:
                raise WorkspaceError(
                    "mail_too_many", "Upload at most 500 .eml files in one .zip.", 422
                )
            if any(e.file_size > MAX_ENTRY_BYTES for e in mails):
                raise _entry_too_large()
            if sum(e.file_size for e in mails) > MAX_TOTAL_BYTES:
                raise _total_too_large()
            total = 0
            for index, entry in enumerate(mails):
                try:
                    with zipped.open(entry) as stream:
                        # One byte more than allowed shows that the entry is larger.
                        raw = stream.read(min(MAX_ENTRY_BYTES, MAX_TOTAL_BYTES - total) + 1)
                except _READ_ERRORS:
                    skipped += 1
                    continue
                if len(raw) > MAX_ENTRY_BYTES:
                    raise _entry_too_large()
                total += len(raw)
                if total > MAX_TOTAL_BYTES:
                    raise _total_too_large()
                # The index orders mails with the same date; the entry name is not kept.
                message = _parse(raw, f"{index:06d}")
                if message is None:
                    skipped += 1
                else:
                    messages.append(message)
    if not messages:
        raise WorkspaceError("no_mail_found", "No readable e-mail was found in the upload.")
    return messages, skipped
