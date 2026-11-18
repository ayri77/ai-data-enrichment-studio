"""The person's own Gmail mailbox as a source of messages, read-only.

Part of the mail extraction probe (docs/32-mail-extraction-probe.md). It is not used by the
application. Sign-in uses Google's own libraries (``google-auth-oauthlib``, the optional
``mail`` extra); every import of ``google.*`` is inside ``GoogleBackend``, so the base
install does not need them. Gmail itself is called over plain HTTPS with the authorized
session of ``google-auth``: one call lists message ids, one reads a message as raw RFC 822,
one reads the address of the mailbox. Nothing here can change a mailbox: the only scope that
is ever requested is ``SCOPE``, and only GET requests are made.

No error text and no log line from this module holds the content of a message, a token or an
address.
"""

import base64
import binascii
import contextlib
import json
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ai_data_enrichment.mail.cleaning import DEFAULT_MAX_CHARS
from ai_data_enrichment.mail.messages import MailMessage, parse_message
from ai_data_enrichment.usage import default_usage_file

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_LIMIT = 200
PAGE_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 30.0
# Pauses before the 2nd, 3rd and 4th try of a request that got 429, 5xx or no answer.
RETRY_PAUSES = (1.0, 2.0, 4.0)
SIGN_IN_TIMEOUT_SECONDS = 300
CLIENT_FILE_ENV = "ENRICHMENT_GMAIL_CLIENT_FILE"
DEFAULT_LABEL = "default"

MISSING_EXTRA_MESSAGE = (
    "Reading Gmail needs the optional 'mail' dependencies; install them with "
    "'uv sync --extra mail' (or add '--extra mail' to 'uv run')."
)
AUTH_MESSAGE = "The Gmail sign-in is no longer accepted or has expired; run again to sign in."
FORBIDDEN_MESSAGE = (
    "Gmail refused the request (HTTP 403). Check that the Gmail API is enabled in the Google "
    "Cloud project and that this account is listed as a test user."
)

_LABEL = re.compile(r"[A-Za-z0-9_-]{1,40}")
# 403 answers that mean "too fast" and not "refused".
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})


class GmailError(Exception):
    """A clear, content-free failure of the Gmail source."""


class MissingExtraError(GmailError):
    """The optional ``mail`` dependencies are not installed."""


class GmailAuthError(GmailError):
    """Gmail answered 401, or a refresh of the sign-in failed on the way: sign in again."""


class GmailForbiddenError(GmailError):
    """Gmail answered 403 for another reason than the rate: a new sign-in would not help."""


class RefreshFailed(Exception):
    """The stored sign-in can no longer be refreshed (revoked, or expired after 7 days)."""


class _NotFound(Exception):
    pass


# --- Token file ----------------------------------------------------------------------


def validate_label(label: str) -> str:
    """The label of an account on this machine: letters, digits, ``-`` and ``_`` only."""
    if not _LABEL.fullmatch(label):
        raise ValueError(
            "--gmail-account may hold only letters, digits, '-' and '_' (1 to 40 characters)"
        )
    return label


def token_path(label: str) -> Path:
    """``<state folder>/ai-data-enrichment-studio/mail/gmail-token-<label>.json``.

    The state folder is the one the usage file lives in (``default_usage_file``); never inside
    the working directory.
    """
    validate_label(label)
    root = default_usage_file().parent.parent
    return root / "mail" / f"gmail-token-{label}.json"


def read_token(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return ""  # unreadable: handled like a broken token


def write_token(path: Path, text: str) -> None:
    """Write the token file atomically with owner-only permissions (0600 where supported)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(temporary, flags, 0o600)
    except FileExistsError:
        temporary.unlink()
        descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def delete_token(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def sign_out(label: str) -> bool:
    """Delete the local token of the label. It does not revoke anything at Google."""
    return delete_token(token_path(label))


# --- Google libraries (optional) -----------------------------------------------------


class Backend(Protocol):
    """What the sign-in needs from the Google libraries; tests use a fake."""

    auth_errors: tuple[type[BaseException], ...]

    def credentials_from_json(self, text: str) -> Any: ...

    def refresh(self, credentials: Any) -> None: ...

    def sign_in(self, client_file: Path) -> Any: ...

    def session(self, credentials: Any) -> Any: ...


class GoogleBackend:
    """The real thing. Imports ``google.*`` here and nowhere else."""

    def __init__(self) -> None:
        try:
            import google.auth.exceptions as exceptions  # pyright: ignore[reportMissingImports]
            import google.auth.transport.requests as transport  # pyright: ignore[reportMissingImports]
            import google.oauth2.credentials as credentials  # pyright: ignore[reportMissingImports]
            import google_auth_oauthlib.flow as flow  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise MissingExtraError(MISSING_EXTRA_MESSAGE) from exc
        self._exceptions = exceptions
        self._transport = transport
        self._credentials = credentials
        self._flow = flow
        # AuthorizedSession refreshes the token itself and raises this when that fails.
        self.auth_errors: tuple[type[BaseException], ...] = (exceptions.RefreshError,)

    def credentials_from_json(self, text: str) -> Any:
        try:
            info = json.loads(text)
            return self._credentials.Credentials.from_authorized_user_info(info, scopes=[SCOPE])
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError("the token file is not usable") from None

    def refresh(self, credentials: Any) -> None:
        try:
            credentials.refresh(self._transport.Request())
        except self._exceptions.RefreshError:
            raise RefreshFailed from None
        except self._exceptions.TransportError:
            raise GmailError(
                "Could not reach Google to renew the sign-in; check the network and run again."
            ) from None

    def sign_in(self, client_file: Path) -> Any:
        try:
            flow = self._flow.InstalledAppFlow.from_client_secrets_file(
                str(client_file), scopes=[SCOPE]
            )
        except (OSError, ValueError, KeyError):
            raise GmailError(
                "The OAuth client file could not be read; download the 'Desktop app' client "
                "JSON again from Google Cloud."
            ) from None
        try:
            return flow.run_local_server(
                port=0,
                open_browser=True,
                timeout_seconds=SIGN_IN_TIMEOUT_SECONDS,
                authorization_prompt_message=(
                    "Your browser should open to sign in to Google. If it does not, open "
                    "this address:\n{url}"
                ),
                success_message="Signed in. You can close this tab and go back to the terminal.",
            )
        except Exception as exc:  # noqa: BLE001 - only the class name is reported
            raise GmailError(
                f"Sign-in to Google did not finish ({type(exc).__name__}); run again to retry."
            ) from None

    def session(self, credentials: Any) -> Any:
        return self._transport.AuthorizedSession(credentials)


def authorize(
    client_file: Path | None,
    label: str,
    backend: Backend,
    *,
    token_file: Path | None = None,
) -> Any:
    """Credentials for the label: the stored token, refreshed if needed, else a new sign-in.

    A token that cannot be read or refreshed (for instance the 7-day expiry of an app in
    testing) is deleted and the sign-in runs again. The client file is needed only then.
    """
    path = token_file or token_path(label)
    credentials = None
    text = read_token(path)
    if text is not None:
        try:
            credentials = backend.credentials_from_json(text)
        except ValueError:
            delete_token(path)
    if credentials is not None and not credentials.valid:
        if credentials.refresh_token:
            try:
                backend.refresh(credentials)
                write_token(path, credentials.to_json())
            except RefreshFailed:
                delete_token(path)
                credentials = None
        else:
            delete_token(path)
            credentials = None
    if credentials is None:
        if client_file is None:
            raise GmailError(
                f"No OAuth client file: give --gmail-client PATH or set {CLIENT_FILE_ENV}."
            )
        credentials = backend.sign_in(client_file)
        write_token(path, credentials.to_json())
    return credentials


# --- Reading messages ----------------------------------------------------------------


@dataclass(frozen=True)
class FetchedMail:
    id: str
    raw: bytes
    message: MailMessage


@dataclass
class GmailFetch:
    mails: list[FetchedMail] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)


def file_name(number: int, message_id: str) -> str:
    """The name a fetched message goes by: in the rows file and as a saved ``.eml``."""
    return f"{number:03d}_{message_id}.eml"


class GmailSource:
    """Lists and reads messages through an authorized HTTP session (GET requests only)."""

    def __init__(
        self,
        session: Any,
        *,
        sleep: Callable[[float], None] = time.sleep,
        auth_errors: tuple[type[BaseException], ...] = (),
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._session = session
        self._sleep = sleep
        self._auth_errors = auth_errors
        self._timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{API}/{path}"
        attempts = len(RETRY_PAUSES) + 1
        status: int | None = None
        for attempt in range(attempts):
            if attempt:
                self._sleep(RETRY_PAUSES[attempt - 1])
            try:
                response = self._session.get(url, params=params, timeout=self._timeout)
            except self._auth_errors:
                raise GmailAuthError(AUTH_MESSAGE) from None
            except OSError:
                status = None
                continue
            code: int = response.status_code
            status = code
            if code == 200:
                try:
                    data = response.json()
                except ValueError:
                    raise GmailError("Gmail sent a reply that is not JSON.") from None
                if not isinstance(data, dict):
                    raise GmailError("Gmail sent a reply in an unexpected shape.")
                return data
            if code == 404:
                raise _NotFound
            if code == 403 and _reason(response) in _RATE_REASONS:
                continue
            if code == 401:
                raise GmailAuthError(AUTH_MESSAGE)
            if code == 403:
                raise GmailForbiddenError(FORBIDDEN_MESSAGE)
            if code == 429 or code >= 500:
                continue
            raise GmailError(f"Gmail refused the request (HTTP {code}).")
        last = f"HTTP {status}" if status is not None else "no connection"
        raise GmailError(
            f"Gmail did not answer after {attempts} tries (last: {last}); try again later."
        )

    def profile_address(self) -> str:
        """The mailbox's own address (allowed by the read-only scope); empty if not given."""
        try:
            data = self._get("profile")
        except _NotFound:
            return ""
        address = data.get("emailAddress")
        return address.strip().lower() if isinstance(address, str) else ""

    def list_ids(self, query: str, limit: int) -> list[str]:
        """Ids of up to ``limit`` messages matching the query, newest first."""
        if not 1 <= limit <= MAX_LIMIT:
            raise GmailError(f"The limit must be between 1 and {MAX_LIMIT} messages.")
        if not query.strip():
            raise GmailError("The Gmail query cannot be empty.")
        ids: list[str] = []
        token: str | None = None
        while len(ids) < limit:
            params: dict[str, Any] = {"q": query, "maxResults": min(PAGE_SIZE, limit - len(ids))}
            if token:
                params["pageToken"] = token
            try:
                data = self._get("messages", params)
            except _NotFound:
                break
            items = data.get("messages", [])
            if not isinstance(items, list):
                raise GmailError("Gmail sent a reply in an unexpected shape.")
            for item in items:
                identifier = item.get("id") if isinstance(item, dict) else None
                if isinstance(identifier, str) and identifier:
                    ids.append(identifier)
            next_token = data.get("nextPageToken")
            if not items or not isinstance(next_token, str) or not next_token:
                break
            token = next_token
        return ids[:limit]

    def fetch(self, ids: Sequence[str], *, max_chars: int = DEFAULT_MAX_CHARS) -> GmailFetch:
        """Read the messages one after another. A message that is gone (404) or cannot be
        read as a mail is skipped and its id kept in ``skipped_ids``."""
        result = GmailFetch()
        for identifier in ids:
            try:
                data = self._get(f"messages/{identifier}", {"format": "raw"})
            except _NotFound:
                result.skipped_ids.append(identifier)
                continue
            raw = _decode_raw(data.get("raw"))
            number = len(result.mails) + 1
            try:
                if raw is None:
                    raise ValueError("no raw message")
                message = parse_message(
                    raw, source=file_name(number, identifier), max_chars=max_chars
                )
            except ValueError:
                result.skipped_ids.append(identifier)
                continue
            result.mails.append(FetchedMail(identifier, raw, message))
        return result


def _reason(response: Any) -> str:
    try:
        return str(response.json()["error"]["errors"][0]["reason"])
    except Exception:  # noqa: BLE001 - an answer without a readable reason has none
        return ""


def _decode_raw(value: object) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        return None


def open_source(
    client_file: Path | None,
    label: str,
    *,
    backend: Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
    token_file: Path | None = None,
) -> GmailSource:
    """Sign in (or reuse the stored token) and return a source for the person's mailbox."""
    backend = backend or GoogleBackend()
    credentials = authorize(client_file, label, backend, token_file=token_file)
    return GmailSource(backend.session(credentials), sleep=sleep, auth_errors=backend.auth_errors)
