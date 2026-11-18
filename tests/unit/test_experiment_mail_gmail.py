"""The mail probe with Gmail as its source: options, the console, files, the summary.

A fake mailbox stands in for Gmail and a fake API for OpenAI; nothing touches the network.
"""

import builtins
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts.experiment_mail_extraction import REPO_ROOT, check_save_dir, ignored_by_git, run
from tests.support.gmail_fakes import FIXTURES, MailboxSession, Reply, raw_of
from tests.support.text_extraction import entry, response
from tests.support.web_lookup import RowApi

from ai_data_enrichment.mail import gmail

QUERY = "category:purchases newer_than:180d"
OWN = "owner.invented@example.org"
TARGETS = ["--target", "Service:text", "--target", "Amount:number"]


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-0123456789")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv(gmail.CLIENT_FILE_ENV, raising=False)


def mailbox(count: int = 3, **kwargs: Any) -> MailboxSession:
    files = sorted(FIXTURES.glob("*.eml"))[:count]
    return MailboxSession({f"id{n}": raw_of(f) for n, f in enumerate(files, 1)}, **kwargs)


class Opener:
    """The ``gmail_opener`` the script is given: records the arguments, returns a source."""

    def __init__(self, session: MailboxSession) -> None:
        self.session = session
        self.opened: list[tuple[Path | None, str]] = []

    def __call__(self, client_file: Path | None, label: str) -> gmail.GmailSource:
        self.opened.append((client_file, label))
        return gmail.GmailSource(self.session, sleep=lambda s: None)


def answer(kwargs: dict[str, Any]) -> Any:
    values = {f"value_{i}": entry() for i in range(1, 3)}
    values["value_1"] = entry("Cloudbox", "Cloudbox")
    return response({"applies": True, "values": values})


def argv(out: Path, *extra: str) -> list[str]:
    return ["--gmail-query", QUERY, "--task", "Payments", *TARGETS, "--out-dir", str(out), *extra]


def fails(args: list[str], capsys: pytest.CaptureFixture[str], opener: Any = None) -> str:
    with pytest.raises(SystemExit) as stop:
        run(args, client=RowApi(answer), gmail_opener=opener or Opener(mailbox()))
    assert stop.value.code == 2
    return capsys.readouterr().err


# --- options ---------------------------------------------------------------------------


def test_exactly_one_source_is_required(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["--task", "q", *TARGETS]
    assert "exactly one of --eml-dir" in fails(base, capsys)
    both = ["--eml-dir", str(FIXTURES), "--gmail-query", QUERY, *base]
    assert "exactly one of --eml-dir" in fails(both, capsys)
    assert "cannot be empty" in fails(["--gmail-query", " ", *base], capsys)


def test_gmail_options_without_a_gmail_query_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    eml = ["--eml-dir", str(FIXTURES), "--task", "q", *TARGETS]
    for extra in (["--save-eml", str(tmp_path)], ["--gmail-account", "work"]):
        assert "go with --gmail-query" in fails([*eml, *extra], capsys)


def test_the_limit_is_capped_for_gmail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert "at most 200" in fails(argv(tmp_path / "o", "--limit", "201"), capsys)
    assert "positive" in fails(argv(tmp_path / "o", "--limit", "0"), capsys)


def test_a_bad_account_label_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert "--gmail-account" in fails(argv(tmp_path / "o", "--gmail-account", "../x"), capsys)


def test_the_default_limit_is_40(tmp_path: Path) -> None:
    opener = Opener(mailbox())
    assert run(argv(tmp_path / "o", "--dry-run"), gmail_opener=opener) == 0
    listing = [params for path, params in opener.session.calls if path == "messages"]
    assert listing[0]["maxResults"] == 40 and listing[0]["q"] == QUERY


# --- save-eml --------------------------------------------------------------------------


def test_save_eml_is_refused_inside_the_repository_outside_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opener = Opener(mailbox())
    for inside in (REPO_ROOT / "docs" / "never-created", REPO_ROOT / "never-created", REPO_ROOT):
        error = fails(argv(tmp_path / "o", "--save-eml", str(inside)), capsys, opener)
        assert "must not be written inside the repository" in error
    assert not (REPO_ROOT / "docs" / "never-created").exists()
    assert not (REPO_ROOT / "never-created").exists()
    assert opener.opened == []  # refused before anything was read


def test_the_rule_for_save_eml(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "artifacts").mkdir(parents=True)
    assert check_save_dir(root / "artifacts" / "mail-extraction" / "mails", root) is None
    assert check_save_dir(tmp_path / "elsewhere", root) is None
    assert check_save_dir(root / "docs", root)
    assert check_save_dir(root, root)
    assert check_save_dir(root / "artifacts" / ".." / "docs", root)  # no way round with ..
    link = tmp_path / "link"
    link.symlink_to(root / "docs")
    assert check_save_dir(link, root)  # a link into the repository counts as inside
    assert check_save_dir(Path("artifacts/x"), REPO_ROOT) is None


def test_the_repository_ignores_artifacts_and_client_and_token_files() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text().splitlines()
    for line in ("artifacts/", "client_secret*.json", "gmail-token*.json"):
        assert line in ignored


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_saved_mails_repeat_the_run_without_gmail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = tmp_path / "mails"
    session = mailbox(3)
    assert run(argv(tmp_path / "a", "--save-eml", str(saved)), RowApi(answer), Opener(session)) == 0
    names = sorted(p.name for p in saved.iterdir())
    assert len(names) == 3 and all(n[:4] == f"{names.index(n) + 1:03d}_" for n in names)
    originals = sorted(FIXTURES.glob("*.eml"))[:3]
    assert [p.read_bytes() for p in sorted(saved.iterdir())] == [p.read_bytes() for p in originals]
    assert all((p.stat().st_mode & 0o777) == 0o600 for p in saved.iterdir())
    assert "deleting them is up to you" in capsys.readouterr().out

    again = RowApi(answer)
    assert run(["--eml-dir", str(saved), "--task", "Payments", *TARGETS, "--out-dir",
                str(tmp_path / "b")], client=again) == 0  # fmt: skip
    first = json.loads((tmp_path / "a" / "rows.json").read_text(encoding="utf-8"))
    second = json.loads((tmp_path / "b" / "rows.json").read_text(encoding="utf-8"))
    assert [r["source"] for r in first] == [r["source"] for r in second]
    assert [r["subject"] for r in first] == [r["subject"] for r in second]


def test_nothing_is_saved_by_default(tmp_path: Path) -> None:
    assert run(argv(tmp_path / "o"), RowApi(answer), Opener(mailbox())) == 0
    assert sorted(p.name for p in (tmp_path / "o").iterdir()) == [
        "register.xlsx",
        "rows.json",
        "summary.json",
    ]


# --- console, dry run -------------------------------------------------------------------


def test_the_console_says_what_will_happen_before_anything_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    order: list[str] = []

    class Late(Opener):
        def __call__(self, client_file: Path | None, label: str) -> gmail.GmailSource:
            order.append(capsys.readouterr().out)
            return super().__call__(client_file, label)

    assert run(argv(tmp_path / "o", "--limit", "7"), RowApi(answer), Late(mailbox())) == 0
    assert (
        f"Reading up to 7 messages matching {QUERY!r} from Gmail, read-only; their cleaned and "
        "masked text will be sent to OpenAI." in order[0]
    )


def test_a_dry_run_with_gmail_reads_gmail_but_never_builds_an_openai_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import openai

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("an OpenAI client was constructed")

    monkeypatch.setattr(openai, "OpenAI", forbidden)
    monkeypatch.delenv("OPENAI_API_KEY")
    api = RowApi(answer)
    opener = Opener(mailbox())
    assert run(argv(tmp_path / "o", "--dry-run"), client=api, gmail_opener=opener) == 0
    assert api.calls == [] and not (tmp_path / "o").exists()
    assert len([c for c in opener.session.calls if c[0].startswith("messages/")]) == 3  # it read
    out = capsys.readouterr().out
    assert "nothing will be sent to OpenAI (--dry-run)" in out
    assert "were read from Gmail; nothing is sent to OpenAI" in out


def test_a_missing_openai_key_stops_a_real_gmail_run_before_the_mailbox_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    opener = Opener(mailbox())
    with pytest.raises(SystemExit):
        run(argv(tmp_path / "o"), gmail_opener=opener)
    assert "OPENAI_API_KEY" in capsys.readouterr().err and opener.opened == []


def test_the_address_the_mailbox_reports_is_masked_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = (
        "From: Shop <shop@example.com>\nTo: someone.else@example.org\nSubject: Receipt\n"
        f"Date: Wed, 04 Nov 2026 11:05:12 +0100\n\nPaid by {OWN}. Amount: 4.50 EUR\n"
    )
    import base64

    raw = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
    opener = Opener(MailboxSession({"only": raw}, address=OWN.upper()))
    assert run(argv(tmp_path / "o", "--dry-run"), gmail_opener=opener) == 0
    out = capsys.readouterr().out
    assert "Paid by [me]." in out and OWN not in out


# --- client file ------------------------------------------------------------------------


def test_the_client_file_comes_from_the_option_or_the_environment_and_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / "client_secret_invented.json"
    opener = Opener(mailbox())
    run(argv(tmp_path / "o", "--dry-run", "--gmail-client", str(outside)), gmail_opener=opener)
    assert opener.opened == [(outside, "default")]
    assert "inside the repository" not in capsys.readouterr().out
    assert not outside.exists()  # nothing was created or copied

    monkeypatch.setenv(gmail.CLIENT_FILE_ENV, str(outside))
    opener = Opener(mailbox())
    run(argv(tmp_path / "o", "--dry-run", "--gmail-account", "work"), gmail_opener=opener)
    assert opener.opened == [(outside, "work")]
    monkeypatch.delenv(gmail.CLIENT_FILE_ENV)
    opener = Opener(mailbox())
    run(argv(tmp_path / "o", "--dry-run"), gmail_opener=opener)
    assert opener.opened == [(None, "default")]  # fine when a stored token exists


def test_a_client_file_inside_the_repository_that_git_does_not_ignore_gets_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inside = REPO_ROOT / "docs" / "oauth_client_invented.json"  # no ignore rule matches it
    run(
        argv(tmp_path / "o", "--dry-run", "--gmail-client", str(inside)),
        gmail_opener=Opener(mailbox()),
    )
    out = capsys.readouterr().out
    assert "is inside the repository and git does not ignore it" in out
    assert "must not be committed" in out


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize(
    "relative",
    ["credentials/client_secret_invented.json", "credentials/anything_invented.json"],
)
def test_a_client_file_in_an_ignored_place_of_the_repository_gets_no_warning(
    relative: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        argv(tmp_path / "o", "--dry-run", "--gmail-client", str(REPO_ROOT / relative)),
        gmail_opener=Opener(mailbox()),
    )
    assert "inside the repository" not in capsys.readouterr().out


def test_git_that_cannot_answer_counts_as_not_ignored(tmp_path: Path) -> None:
    # A folder that is no clone: git cannot say, so the warning is given.
    assert not ignored_by_git(tmp_path / "client.json", root=tmp_path)


# --- failures ---------------------------------------------------------------------------


def test_refused_access_prints_the_clear_sentence_deletes_the_token_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gmail.write_token(gmail.token_path("default"), "{}")
    session = MailboxSession({})
    session._handler = lambda path, params: Reply(401, {})  # type: ignore[attr-defined]
    assert run(argv(tmp_path / "o"), RowApi(answer), Opener(session)) == 1
    err = capsys.readouterr().err
    assert gmail.AUTH_MESSAGE in err and "was deleted" in err
    assert not gmail.token_path("default").exists()
    assert not (tmp_path / "o").exists()


def test_a_refusal_keeps_the_stored_sign_in_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gmail.write_token(gmail.token_path("default"), "{}")
    session = MailboxSession({})
    session._handler = lambda path, params: Reply(403, {})  # type: ignore[attr-defined]
    assert run(argv(tmp_path / "o"), RowApi(answer), Opener(session)) == 1
    err = capsys.readouterr().err
    assert gmail.FORBIDDEN_MESSAGE in err and "was deleted" not in err
    assert gmail.token_path("default").exists()
    assert not (tmp_path / "o").exists()


def test_a_query_without_results_ends_without_asking_openai(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = RowApi(answer)
    assert run(argv(tmp_path / "o"), api, Opener(MailboxSession({}))) == 1
    assert api.calls == [] and "No readable messages" in capsys.readouterr().err


def test_the_missing_extra_ends_with_one_sentence_before_anything_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_import = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "google" or name.startswith(("google.", "google_auth_oauthlib")):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    for name in [n for n in sys.modules if n == "google" or n.startswith(("google.", "google_"))]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(builtins, "__import__", refuse)
    assert run(argv(tmp_path / "o")) == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == gmail.MISSING_EXTRA_MESSAGE
    assert "Reading up to" not in captured.out  # nothing was announced, nothing was started


# --- summary ----------------------------------------------------------------------------


def test_the_summary_records_the_source_but_not_the_account(tmp_path: Path) -> None:
    session = mailbox(3, address=OWN)
    session.messages["gone"] = "x"  # listed, but 404 when read: see below
    inner = session._handler

    def handler(path: str, params: dict[str, Any]) -> Reply:
        if path == "messages/gone":
            return Reply(404, {})
        return inner(path, params)  # type: ignore[return-value]

    session._handler = handler  # type: ignore[attr-defined]
    out = tmp_path / "o"
    assert run(argv(out, "--save-eml", str(tmp_path / "m")), RowApi(answer), Opener(session)) == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["source"] == {
        "kind": "gmail",
        "query": QUERY,
        "listed": 4,
        "fetched": 3,
        "skipped": 1,
        "saved_eml": True,
    }
    assert summary["messages"]["skipped"] == 1 and summary["messages"]["asked"] == 3
    for name in ("summary.json", "rows.json"):
        text = (out / name).read_text(encoding="utf-8")
        assert OWN not in text and str(tmp_path / "m") not in text


def test_the_summary_of_a_folder_run_says_eml(tmp_path: Path) -> None:
    out = tmp_path / "o"
    args = ["--eml-dir", str(FIXTURES), "--task", "q", *TARGETS, "--out-dir", str(out)]
    assert run(args, client=RowApi(answer)) == 0
    source = json.loads((out / "summary.json").read_text(encoding="utf-8"))["source"]
    assert source == {
        "kind": "eml",
        "query": None,
        "listed": 10,
        "fetched": 10,
        "skipped": 0,
        "saved_eml": False,
    }


# --- sign out ---------------------------------------------------------------------------


def test_sign_out_deletes_the_token_of_the_label_and_nothing_else(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gmail.write_token(gmail.token_path("work"), "{}")
    gmail.write_token(gmail.token_path("default"), "{}")
    assert run(["--gmail-sign-out", "--gmail-account", "work"]) == 0
    assert not gmail.token_path("work").exists() and gmail.token_path("default").exists()
    assert "Deleted the stored Gmail sign-in 'work'" in capsys.readouterr().out
    assert run(["--gmail-sign-out", "--gmail-account", "work"]) == 0
    assert "No stored Gmail sign-in 'work'" in capsys.readouterr().out


def test_sign_out_is_used_alone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert "used alone" in fails(["--gmail-sign-out", "--gmail-query", QUERY], capsys)
