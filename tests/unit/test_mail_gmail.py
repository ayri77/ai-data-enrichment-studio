"""The Gmail source of the mail probe: paging, errors, the token file, the sign-in.

No network and no Google library: a fake session with canned answers, a fake backend, and
stand-in modules for ``google.*`` where the real backend class itself is tried.
"""

import builtins
import json
import re
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from tests.support.gmail_fakes import (
    BASE,
    FIXTURES,
    FakeBackend,
    FakeCredentials,
    FakeSession,
    MailboxSession,
    Reply,
    raw_of,
)

from ai_data_enrichment.mail import gmail
from ai_data_enrichment.mail.messages import parse_message
from ai_data_enrichment.usage import default_usage_file

SECRET_SUBJECT = "Hidden-Subject-Zebra-7731"


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


def source(session: FakeSession, sleeps: list[float] | None = None) -> gmail.GmailSource:
    sleeps = sleeps if sleeps is not None else []
    return gmail.GmailSource(session, sleep=sleeps.append)


def mailbox(count: int, **kwargs: Any) -> MailboxSession:
    raw = raw_of(next(FIXTURES.glob("01_*.eml")))
    return MailboxSession({f"id{n:02d}": raw for n in range(1, count + 1)}, **kwargs)


# --- listing ---------------------------------------------------------------------------


def test_listing_pages_until_the_limit_and_asks_for_no_more_than_is_needed() -> None:
    session = mailbox(9, page=2)
    query = "category:purchases newer_than:180d"
    ids = source(session).list_ids(query, 5)
    assert ids == ["id01", "id02", "id03", "id04", "id05"]
    assert session.calls == [
        ("messages", {"q": query, "maxResults": 5}),
        ("messages", {"q": query, "maxResults": 3, "pageToken": "2"}),
        ("messages", {"q": query, "maxResults": 1, "pageToken": "4"}),
    ]


def test_listing_stops_when_there_is_no_next_page_or_nothing_matches() -> None:
    assert source(mailbox(3, page=2)).list_ids("q", 50) == ["id01", "id02", "id03"]
    session = FakeSession(lambda path, params: Reply(200, {"resultSizeEstimate": 0}))
    assert source(session).list_ids("q", 10) == []
    assert len(session.calls) == 1


@pytest.mark.parametrize("limit", [0, -1, gmail.MAX_LIMIT + 1, 500])
def test_the_limit_has_a_hard_cap_and_nothing_is_requested_above_it(limit: int) -> None:
    session = mailbox(3)
    with pytest.raises(gmail.GmailError, match=f"between 1 and {gmail.MAX_LIMIT}"):
        source(session).list_ids("q", limit)
    assert session.calls == []


def test_the_cap_itself_is_allowed() -> None:
    assert gmail.MAX_LIMIT == 200
    assert source(mailbox(2)).list_ids("q", 200) == ["id01", "id02"]


def test_an_empty_query_is_refused() -> None:
    with pytest.raises(gmail.GmailError, match="query"):
        source(mailbox(1)).list_ids("  ", 5)


def test_the_profile_address_is_read_and_lowercased() -> None:
    session = mailbox(1, address="Owner.Invented@Example.org")
    assert source(session).profile_address() == "owner.invented@example.org"
    assert session.calls == [("profile", {})]


# --- reading ---------------------------------------------------------------------------


def test_raw_is_decoded_and_read_by_the_same_parser_as_a_file() -> None:
    path = next(FIXTURES.glob("01_*.eml"))
    session = mailbox(2)
    fetched = source(session).fetch(["id01", "id02"])
    assert [m.id for m in fetched.mails] == ["id01", "id02"]
    assert fetched.skipped_ids == []
    assert fetched.mails[0].raw == path.read_bytes()
    assert fetched.mails[0].message == parse_message(path.read_bytes(), source="001_id01.eml")
    assert fetched.mails[1].message.source == "002_id02.eml"
    assert fetched.mails[0].message.subject.startswith("Ihre Rechnung")
    assert session.calls == [
        ("messages/id01", {"format": "raw"}),
        ("messages/id02", {"format": "raw"}),
    ]


def test_fetch_passes_max_chars_to_the_parser() -> None:
    fetched = source(mailbox(1)).fetch(["id01"], max_chars=200)
    assert fetched.mails[0].message.truncated is True


def test_a_message_that_is_gone_is_skipped_and_counted_and_numbering_stays_dense() -> None:
    fetched = source(mailbox(2)).fetch(["id01", "gone", "id02"])
    assert fetched.skipped_ids == ["gone"]
    assert [m.message.source for m in fetched.mails] == ["001_id01.eml", "002_id02.eml"]


def test_a_reply_that_is_not_a_mail_is_skipped_too() -> None:
    answers = {"a": {"raw": "!!!not base64!!!"}, "b": {}, "c": {"raw": ""}}
    session = FakeSession(lambda path, params: Reply(200, answers[path.removeprefix("messages/")]))
    fetched = source(session).fetch(["a", "b", "c"])
    assert fetched.mails == [] and fetched.skipped_ids == ["a", "b", "c"]


# --- errors ----------------------------------------------------------------------------


def failing(status: int, reason: str | None = None) -> FakeSession:
    body: dict[str, Any] = {"error": {"code": status, "message": SECRET_SUBJECT}}
    if reason:
        body["error"]["errors"] = [{"reason": reason, "message": SECRET_SUBJECT}]
    return FakeSession(lambda path, params: Reply(status, body))


def test_an_expired_sign_in_gives_one_clear_error_without_retry() -> None:
    session, sleeps = failing(401), []
    with pytest.raises(gmail.GmailAuthError) as caught:
        source(session, sleeps).fetch(["id01"])
    assert str(caught.value) == gmail.AUTH_MESSAGE
    assert "run again to sign in" in str(caught.value)
    assert len(session.calls) == 1 and sleeps == []


def test_a_refusal_is_not_an_expired_sign_in_and_is_not_retried() -> None:
    # A 403 that is not about the rate: the API is off in the project, or the account is not
    # a test user. Signing in again would not help, so it is another error.
    session, sleeps = failing(403), []
    with pytest.raises(gmail.GmailForbiddenError) as caught:
        source(session, sleeps).fetch(["id01"])
    assert str(caught.value) == gmail.FORBIDDEN_MESSAGE
    assert not isinstance(caught.value, gmail.GmailAuthError)
    assert len(session.calls) == 1 and sleeps == []


def test_too_many_requests_are_retried_a_fixed_number_of_times_then_fail() -> None:
    session, sleeps = failing(429), []
    with pytest.raises(gmail.GmailError, match="did not answer after 4 tries") as caught:
        source(session, sleeps).list_ids("q", 5)
    assert not isinstance(caught.value, gmail.GmailAuthError)
    assert len(session.calls) == 4
    assert sleeps == list(gmail.RETRY_PAUSES)
    assert "HTTP 429" in str(caught.value)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_server_errors_are_retried_too(status: int) -> None:
    session, sleeps = failing(status), []
    with pytest.raises(gmail.GmailError, match=f"HTTP {status}"):
        source(session, sleeps).profile_address()
    assert len(session.calls) == 4


def test_a_retry_that_succeeds_returns_the_answer() -> None:
    answers = iter([Reply(429, {}), Reply(503, {}), Reply(200, {"emailAddress": "a@example.org"})])
    sleeps: list[float] = []
    assert source(FakeSession(lambda p, q: next(answers)), sleeps).profile_address() == (
        "a@example.org"
    )
    assert sleeps == [1.0, 2.0]


def test_a_403_that_means_too_fast_is_retried_and_not_taken_for_a_refusal() -> None:
    session, sleeps = failing(403, "userRateLimitExceeded"), []
    with pytest.raises(gmail.GmailError, match="did not answer") as caught:
        source(session, sleeps).list_ids("q", 5)
    assert not isinstance(caught.value, gmail.GmailAuthError)
    assert len(session.calls) == 4


def test_a_dropped_connection_is_retried_then_reported_without_details() -> None:
    session = FakeSession(lambda p, q: ConnectionError(f"boom {SECRET_SUBJECT}"))
    with pytest.raises(gmail.GmailError, match="no connection") as caught:
        source(session).list_ids("q", 5)
    assert SECRET_SUBJECT not in str(caught.value) and len(session.calls) == 4


def test_another_refusal_is_reported_with_its_status_only() -> None:
    with pytest.raises(gmail.GmailError, match=r"HTTP 400"):
        source(failing(400)).list_ids("q", 5)


def test_an_error_of_the_sign_in_library_is_the_same_clear_error() -> None:
    class Expired(Exception):
        pass

    def handler(path: str, params: dict[str, Any]) -> Exception:
        return Expired(SECRET_SUBJECT)

    client = gmail.GmailSource(FakeSession(handler), sleep=lambda s: None, auth_errors=(Expired,))
    with pytest.raises(gmail.GmailAuthError) as caught:
        client.profile_address()
    assert str(caught.value) == gmail.AUTH_MESSAGE and caught.value.__cause__ is None


def test_no_error_text_holds_message_content() -> None:
    texts: list[str] = []
    for status, reason in [(401, None), (403, None), (429, None), (500, None), (400, None)]:
        with pytest.raises(gmail.GmailError) as caught:
            source(failing(status, reason)).fetch(["id01"])
        texts.append(str(caught.value))
    odd = FakeSession(lambda p, q: Reply(200, ["list"]))
    with pytest.raises(gmail.GmailError) as caught:
        source(odd).fetch(["id01"])
    texts.append(str(caught.value))
    assert all(SECRET_SUBJECT not in text and "Rechnung" not in text for text in texts)


def test_only_get_requests_to_the_gmail_api_are_made() -> None:
    class OnlyGet:
        calls: list[str] = []

        def get(self, url: str, params: Any = None, timeout: float = 0) -> Reply:
            self.calls.append(url)
            return Reply(200, {"messages": []})

    session = OnlyGet()  # no post, put, delete, patch: any such call would raise
    gmail.GmailSource(session).list_ids("q", 5)
    assert session.calls == [f"{BASE}/messages"]


# --- token file ------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["default", "work", "Sam-2", "a_b", "x" * 40])
def test_good_labels(label: str) -> None:
    assert gmail.validate_label(label) == label


@pytest.mark.parametrize("label", ["", "a/b", "..", "../x", "a b", "a.b", "é", "x" * 41, "a\n"])
def test_bad_labels_are_refused(label: str) -> None:
    with pytest.raises(ValueError, match="--gmail-account"):
        gmail.validate_label(label)
    with pytest.raises(ValueError):
        gmail.token_path(label)


def test_the_token_lives_in_the_state_folder_next_to_the_usage_folder(_state: Path) -> None:
    path = gmail.token_path("work")
    assert path == _state / "ai-data-enrichment-studio" / "mail" / "gmail-token-work.json"
    assert path.parent.parent == default_usage_file().parent.parent
    assert gmail.token_path("a") != gmail.token_path("b")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_the_token_is_written_for_the_owner_only_and_atomically() -> None:
    path = gmail.token_path("work")
    gmail.write_token(path, '{"a": 1}')
    assert path.read_text() == '{"a": 1}'
    assert (path.stat().st_mode & 0o777) == 0o600
    gmail.write_token(path, '{"a": 2}')  # replaces
    assert path.read_text() == '{"a": 2}' and (path.stat().st_mode & 0o777) == 0o600
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temporary file left


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_stale_temporary_file_does_not_block_writing() -> None:
    path = gmail.token_path("work")
    path.parent.mkdir(parents=True)
    stale = path.with_name(path.name + ".tmp")
    stale.write_text("old")
    gmail.write_token(path, "new")
    assert path.read_text() == "new" and not stale.exists()


def client_file(tmp_path: Path) -> Path:
    return tmp_path / "client_secret_invented.json"


def test_a_stored_valid_token_is_used_without_refresh_or_sign_in(tmp_path: Path) -> None:
    path = gmail.token_path("work")
    gmail.write_token(path, "{}")
    stored = FakeCredentials(valid=True)
    backend = FakeBackend(stored=stored)
    assert gmail.authorize(None, "work", backend) is stored
    assert backend.events == ["load"]


def test_an_expired_token_is_refreshed_and_stored(tmp_path: Path) -> None:
    path = gmail.token_path("work")
    gmail.write_token(path, "{}")
    backend = FakeBackend(stored=FakeCredentials(valid=False))
    credentials = gmail.authorize(None, "work", backend)
    assert backend.events == ["load", "refresh"]
    assert credentials.stamp == "refreshed"
    assert json.loads(path.read_text()) == {"stamp": "refreshed"}


def test_a_failed_refresh_deletes_the_token_and_signs_in_again(tmp_path: Path) -> None:
    path = gmail.token_path("work")
    gmail.write_token(path, "{}")
    backend = FakeBackend(stored=FakeCredentials(valid=False), refresh_fails=True)
    credentials = gmail.authorize(client_file(tmp_path), "work", backend)
    assert backend.events == ["load", "refresh", "sign_in:client_secret_invented.json"]
    assert credentials.stamp == "signed-in"
    assert json.loads(path.read_text()) == {"stamp": "signed-in"}  # the new one, stored


def test_a_failed_refresh_without_a_client_file_deletes_the_token_and_says_what_is_missing() -> (
    None
):
    path = gmail.token_path("work")
    gmail.write_token(path, "{}")
    backend = FakeBackend(stored=FakeCredentials(valid=False), refresh_fails=True)
    with pytest.raises(gmail.GmailError, match="--gmail-client"):
        gmail.authorize(None, "work", backend)
    assert not path.exists()


def test_a_token_without_a_refresh_token_or_that_cannot_be_read_means_a_new_sign_in(
    tmp_path: Path,
) -> None:
    path = gmail.token_path("work")
    for backend in (
        FakeBackend(stored=FakeCredentials(valid=False, refresh_token=None)),
        FakeBackend(stored=None),
    ):
        gmail.write_token(path, "garbage")
        gmail.authorize(client_file(tmp_path), "work", backend)
        assert backend.events[-1].startswith("sign_in")
        assert "refresh" not in backend.events
        assert json.loads(path.read_text()) == {"stamp": "signed-in"}


def test_the_first_sign_in_stores_the_token() -> None:
    path = gmail.token_path("fresh")
    backend = FakeBackend()
    gmail.authorize(Path("client.json"), "fresh", backend)
    assert backend.events == ["sign_in:client.json"] and path.exists()


def test_a_sign_in_that_fails_stores_nothing(tmp_path: Path) -> None:
    class Failing(FakeBackend):
        def sign_in(self, client_file: Path) -> Any:
            raise gmail.GmailError("Sign-in to Google did not finish (X); run again to retry.")

    with pytest.raises(gmail.GmailError, match="did not finish"):
        gmail.authorize(client_file(tmp_path), "work", Failing())
    assert not gmail.token_path("work").exists()


def test_sign_out_deletes_the_token_of_that_label_only() -> None:
    gmail.write_token(gmail.token_path("a"), "{}")
    gmail.write_token(gmail.token_path("b"), "{}")
    assert gmail.sign_out("a") is True
    assert not gmail.token_path("a").exists() and gmail.token_path("b").exists()
    assert gmail.sign_out("a") is False


# --- the real backend class, against stand-in modules ------------------------------------


class StubGoogle:
    """Stand-ins for the four ``google.*`` modules; records every scope and argument."""

    def __init__(self) -> None:
        self.scopes: list[list[str]] = []
        self.run_kwargs: dict[str, Any] = {}
        self.refresh_error: Exception | None = None
        stub = self

        class RefreshError(Exception):
            pass

        class TransportError(Exception):
            pass

        class Credentials:
            refresh_token = "r"

            @classmethod
            def from_authorized_user_info(cls, info: Any, scopes: Any = None) -> "Credentials":
                stub.scopes.append(list(scopes))
                return cls()

            def refresh(self, request: Any) -> None:
                if stub.refresh_error:
                    raise stub.refresh_error

            def to_json(self) -> str:
                return "{}"

        class Flow:
            def run_local_server(self, **kwargs: Any) -> Any:
                stub.run_kwargs = kwargs
                return Credentials()

        class InstalledAppFlow:
            @staticmethod
            def from_client_secrets_file(path: str, scopes: Any, **kwargs: Any) -> Flow:
                stub.scopes.append(list(scopes))
                if path.endswith("missing.json"):
                    raise FileNotFoundError(path)
                return Flow()

        class AuthorizedSession:
            def __init__(self, credentials: Any) -> None:
                self.credentials = credentials

        self.RefreshError, self.TransportError = RefreshError, TransportError
        self.modules = {
            "google": types.ModuleType("google"),
            "google.auth": types.ModuleType("google.auth"),
            "google.auth.exceptions": types.ModuleType("google.auth.exceptions"),
            "google.auth.transport": types.ModuleType("google.auth.transport"),
            "google.auth.transport.requests": types.ModuleType("google.auth.transport.requests"),
            "google.oauth2": types.ModuleType("google.oauth2"),
            "google.oauth2.credentials": types.ModuleType("google.oauth2.credentials"),
            "google_auth_oauthlib": types.ModuleType("google_auth_oauthlib"),
            "google_auth_oauthlib.flow": types.ModuleType("google_auth_oauthlib.flow"),
        }
        m = self.modules
        m["google.auth.exceptions"].RefreshError = RefreshError  # type: ignore[attr-defined]
        m["google.auth.exceptions"].TransportError = TransportError  # type: ignore[attr-defined]
        m["google.auth.transport.requests"].Request = lambda: "request"  # type: ignore[attr-defined]
        m["google.auth.transport.requests"].AuthorizedSession = AuthorizedSession  # type: ignore[attr-defined]
        m["google.oauth2.credentials"].Credentials = Credentials  # type: ignore[attr-defined]
        m["google_auth_oauthlib.flow"].InstalledAppFlow = InstalledAppFlow  # type: ignore[attr-defined]
        for name, module in m.items():
            parent, _, child = name.rpartition(".")
            if parent:
                setattr(m[parent], child, module)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> StubGoogle:
    google = StubGoogle()
    for name, module in google.modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return google


def test_the_scope_is_read_only_and_the_only_one_ever_passed(stub: StubGoogle) -> None:
    assert gmail.SCOPE == "https://www.googleapis.com/auth/gmail.readonly"
    backend = gmail.GoogleBackend()
    backend.credentials_from_json('{"refresh_token": "r"}')
    backend.sign_in(Path("client.json"))
    assert stub.scopes == [[gmail.SCOPE], [gmail.SCOPE]]
    assert stub.run_kwargs["port"] == 0 and stub.run_kwargs["open_browser"] is True
    # and no other scope is written anywhere in the module
    text = Path(gmail.__file__).read_text(encoding="utf-8")
    assert set(re.findall(r"https://www\.googleapis\.com/auth/[\w.]+", text)) == {gmail.SCOPE}


def test_a_refresh_that_google_refuses_is_a_refresh_failure(stub: StubGoogle) -> None:
    backend = gmail.GoogleBackend()
    credentials = backend.credentials_from_json('{"refresh_token": "r"}')
    backend.refresh(credentials)  # works
    stub.refresh_error = stub.RefreshError("invalid_grant")
    with pytest.raises(gmail.RefreshFailed):
        backend.refresh(credentials)
    stub.refresh_error = stub.TransportError("down")
    with pytest.raises(gmail.GmailError, match="Could not reach Google") as caught:
        backend.refresh(credentials)
    assert not isinstance(caught.value, gmail.RefreshFailed)
    assert backend.auth_errors == (stub.RefreshError,)


def test_an_unreadable_client_file_is_a_clear_error(stub: StubGoogle) -> None:
    with pytest.raises(gmail.GmailError, match="client file could not be read"):
        gmail.GoogleBackend().sign_in(Path("missing.json"))


def test_a_sign_in_that_does_not_finish_names_only_the_class_of_the_failure(
    stub: StubGoogle,
) -> None:
    backend = gmail.GoogleBackend()
    flow = types.SimpleNamespace(
        run_local_server=lambda **kw: (_ for _ in ()).throw(RuntimeError("code=SECRET-CODE"))
    )
    stub.modules["google_auth_oauthlib.flow"].InstalledAppFlow.from_client_secrets_file = (  # type: ignore[attr-defined]
        lambda path, scopes: flow
    )
    with pytest.raises(gmail.GmailError, match="RuntimeError") as caught:
        backend.sign_in(Path("client.json"))
    assert "SECRET-CODE" not in str(caught.value)


def test_a_token_file_that_is_not_json_is_not_usable(stub: StubGoogle) -> None:
    with pytest.raises(ValueError):
        gmail.GoogleBackend().credentials_from_json("not json")


def test_the_missing_extra_is_one_clear_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "google" or name.startswith(("google.", "google_auth_oauthlib")):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    for name in [n for n in sys.modules if n == "google" or n.startswith(("google.", "google_"))]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(gmail.MissingExtraError) as caught:
        gmail.GoogleBackend()
    assert str(caught.value) == gmail.MISSING_EXTRA_MESSAGE
    assert "uv sync --extra mail" in gmail.MISSING_EXTRA_MESSAGE
    assert "\n" not in gmail.MISSING_EXTRA_MESSAGE


def test_importing_the_module_needs_no_google_library() -> None:
    text = Path(gmail.__file__).read_text(encoding="utf-8")
    top_level = [line for line in text.splitlines() if re.match(r"(import|from) google", line)]
    assert top_level == []
