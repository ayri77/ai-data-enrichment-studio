"""The acceptance script ``scripts/mail_register_via_app.py`` against the real application.

``main`` runs against the real FastAPI app in this process (through the test client), the real
run worker and the real handler; only the model client is fake, made inside the worker as in
``test_extraction_api``. The mails are the invented fixtures of ``samples/probe/mail``.
"""

import json
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from scripts import mail_register_via_app as script
from tests.support.extraction_api import LoggedExtractionApiFactory, calls, forbidden_extraction_api
from tests.support.mail_register_api import fixture_answer
from tests.support.mail_table import FIXTURES

from ai_data_enrichment import plan_run
from ai_data_enrichment.api import create_app
from ai_data_enrichment.evaluation.plan_run_fixtures import offline_executor
from ai_data_enrichment.mail.messages import read_eml_folder
from ai_data_enrichment.plan_run import PlanRunService, PlanRunSettings
from ai_data_enrichment.providers.http_transport import UrllibJsonTransport

KEY = "sk-test-key-not-a-secret-0123456789"
TARGETS = ["--target", "Service:text", "--target", "Amount:number", "--target", "Paid on:date"]
TASK = "Payments: what was paid, to whom and when"


@pytest.fixture(autouse=True)
def nothing_real(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        pytest.fail("An offline test attempted external HTTP")

    monkeypatch.setattr(UrllibJsonTransport, "get_json", denied)
    monkeypatch.setattr(UrllibJsonTransport, "post_json", denied)
    monkeypatch.setattr(plan_run, "openai_extraction_api", forbidden_extraction_api)
    monkeypatch.setattr(plan_run, "openai_api", forbidden_extraction_api)
    monkeypatch.setattr(
        urllib.request.OpenerDirector,
        "open",
        lambda *a, **k: pytest.fail("A test reached the real HTTP transport"),
    )
    monkeypatch.delenv("OPENROUTESERVICE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


class ClientTransport:
    """The script's transport, answered by the application through the test client."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.sent: list[tuple[str, str, bytes | None]] = []

    def request(
        self, method: str, path: str, body: bytes | None = None, content_type: str | None = None
    ) -> script.Reply:
        self.sent.append((method, path, body))
        headers = {"content-type": content_type} if content_type else {}
        answer = self.client.request(method, path, content=body, headers=headers)
        return script.Reply(answer.status_code, answer.content)

    def paths(self) -> list[str]:
        return [f"{method} {path.split('?')[0]}" for method, path, _ in self.sent]


@contextmanager
def application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mail: bool = True,
    key: str | None = KEY,
    network: bool = False,
    offline: bool = True,
) -> Iterator[tuple[ClientTransport, PlanRunService, Path]]:
    if key:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    else:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    log = tmp_path / "requests.jsonl"
    app = create_app(
        settings=PlanRunSettings(allow_mail=mail, allow_network=network, usage_file="off"),
        local_browser_configuration=True,
        offline_executor_factory=offline_executor if offline else None,
        offline_extraction_api=LoggedExtractionApiFactory(KEY, log, answer=fixture_answer),
    )
    with TestClient(app) as client:
        yield ClientTransport(client), app.state.plan_run, log


def run(
    capsys: pytest.CaptureFixture[str],
    transport: script.Transport,
    out: Path,
    *extra: str,
    folder: Path = FIXTURES,
    consent: bool = True,
) -> tuple[int, str]:
    argv = ["--eml-dir", str(folder), "--task", TASK, *TARGETS, "--out", str(out), *extra]
    if consent:
        argv.append("--allow-network")
    code = script.main(argv, transport, pause=lambda seconds: None)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def mail_strings() -> list[str]:
    """Everything of the fixture mails the script must never print."""
    strings = [path.name for path in FIXTURES.glob("*.eml")]
    for message in read_eml_folder(FIXTURES):
        strings += [message.subject, message.sender_address, message.text]
        strings += [message.sender_name, *message.text.splitlines()[:12]]
    return [s for s in strings if s and len(s) > 5]


# --- The happy path ----------------------------------------------------------------


def test_the_ten_fixture_mails_become_a_register_through_the_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "result"
    with application(tmp_path, monkeypatch) as (transport, service, log):
        code, shown = run(capsys, transport, out)

        assert code == 0, shown
        assert sorted(p.name for p in out.iterdir()) == [
            "register.xlsx",
            "report.json",
            "summary.json",
        ]
        # The public API only, in order: limits, upload, plan, approve, run, poll, download.
        [plan_id] = service.plans
        [job_id] = service.jobs
        paths = transport.paths()
        assert paths[:5] == [
            "GET /api/limits",
            "POST /api/mail-datasets",
            "POST /api/extraction-plans",
            f"POST /api/plans/{plan_id}/approve",
            f"POST /api/plans/{plan_id}/runs",
        ]
        assert set(paths[5:-2]) == {f"GET /api/jobs/{job_id}"}
        assert paths[-2:] == [
            f"GET /api/jobs/{job_id}/artifacts/result",
            f"GET /api/jobs/{job_id}/artifacts/report",
        ]
        # No file name leaves the folder: the archive has neutral entry names.
        upload = next(body for method, path, body in transport.sent if "mail-datasets" in path)
        assert upload is not None
        with zipfile.ZipFile(BytesIO(upload)) as archive:
            assert archive.namelist() == [f"{n:04d}.eml" for n in range(1, 11)]
            assert {i.compress_type for i in archive.infolist()} == {zipfile.ZIP_STORED}

        summary = json.loads((out / "summary.json").read_text("utf-8"))
        assert summary == json.loads(json.dumps(summary))  # plain JSON
        assert summary["mails"]["read"] == 10 and summary["mails"]["skipped"] == 0
        assert summary["requests"] == 10 == len(calls(log))
        # The trial notice names no amount or date: about the task, nothing to trace.
        assert summary["job_status"] == "needs_review"
        assert summary["rows"] == {"found": 7, "nothing_found": 1, "not_applicable": 2, "failed": 0}
        assert summary["values_dropped_by_reason"] == {"quote_not_in_text": 1}
        assert sum(summary["values_by_basis"].values()) > 8
        assert summary["input_tokens"] > 0 and summary["output_tokens"] > 0
        assert isinstance(summary["elapsed_seconds"], float)

        report = json.loads((out / "report.json").read_text("utf-8"))
        assert report["text_extraction"]["requests"] == 10
        sheet = load_workbook(out / "register.xlsx")["Mail"]
        assert [c.value for c in sheet[1]][-3:] == ["Service", "Amount", "Paid on"]
        # The table's text column is emptied in the result of a table built from mail.
        assert all(cell.value is None for cell in sheet["D"][1:])
        assert any(isinstance(cell.value, float) for cell in sheet["F"][1:])

    for text in mail_strings():
        assert text not in shown, "mail content reached the console"
    assert TASK not in shown
    assert "register.xlsx" in shown and str(out.resolve()) in shown
    assert not (tmp_path / "result" / "rows.json").exists()


def test_limit_takes_the_first_files_in_name_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with application(tmp_path, monkeypatch) as (transport, _, log):
        code, shown = run(capsys, transport, tmp_path / "out", "--limit", "3")
        assert code == 0, shown
        assert len(calls(log)) == 3
        upload = next(body for _, path, body in transport.sent if "mail-datasets" in path)
        assert upload is not None
        with zipfile.ZipFile(BytesIO(upload)) as archive:
            assert archive.namelist() == ["0001.eml", "0002.eml", "0003.eml"]
            first = archive.read("0001.eml")
        assert first == (FIXTURES / "01_cloudbox_rechnung_de.eml").read_bytes()


# --- Refused before anything is sent -------------------------------------------------


@pytest.mark.parametrize(
    ("options", "named"),
    [
        ({"mail": False}, "ENRICHMENT_ALLOW_MAIL=1"),
        ({"key": None}, "OPENAI_API_KEY"),
        ({"offline": False}, "--allow-network"),
    ],
)
def test_the_limits_decide_before_anything_is_uploaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    options: dict[str, Any],
    named: str,
) -> None:
    out = tmp_path / "out"
    with application(tmp_path, monkeypatch, **options) as (transport, service, log):
        code, shown = run(capsys, transport, out)
        assert code == script.EXIT_NOT_SENT
        assert named in shown
        # The limits and, to name the setting, the open provider status: nothing else.
        assert set(transport.paths()) <= {"GET /api/limits", "GET /api/provider-settings"}
        assert service.datasets == {} and service.plans == {} and not log.exists()
    assert not out.exists()


def test_a_server_row_limit_below_the_number_of_mails_names_its_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(plan_run, "MAX_RUN_ROWS", 5)
    with application(tmp_path, monkeypatch) as (transport, service, _):
        code, shown = run(capsys, transport, tmp_path / "out")
        assert code == script.EXIT_NOT_SENT
        assert "ENRICHMENT_MAX_RUN_ROWS to at least 10" in shown
        assert transport.paths() == ["GET /api/limits"] and service.datasets == {}
        # --limit brings it under the limit.
        code, shown = run(capsys, transport, tmp_path / "out", "--limit", "5")
        assert code == 0, shown


def test_without_the_consent_flag_nothing_is_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with application(tmp_path, monkeypatch) as (transport, _, _):
        code, shown = run(capsys, transport, tmp_path / "out", consent=False)
        assert code == script.EXIT_NOT_SENT and "--allow-network" in shown
        assert transport.sent == []


def test_an_output_folder_inside_the_repository_that_git_does_not_ignore_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Silent:
        def request(self, *args: Any, **kwargs: Any) -> script.Reply:
            pytest.fail("Something was sent although the output folder was refused")

    unignored = script.REPO_ROOT / "docs" / "mail-output-not-ignored"
    with pytest.raises(SystemExit) as stop:
        run(capsys, Silent(), unignored)
    assert stop.value.code == 2
    assert not unignored.exists()
    assert "git does not ignore it" in capsys.readouterr().err
    # The default folder is under artifacts/, which git ignores, and a folder outside is fine.
    default = script.REPO_ROOT / script.DEFAULT_OUT / "20260101T000000Z"
    assert script.ignored_by_git(default)
    assert not script.inside(tmp_path, script.REPO_ROOT)


@pytest.mark.parametrize(
    "argv",
    [
        ["--url", "http://example.com:8010"],
        ["--url", "ftp://127.0.0.1"],
        ["--limit", "0"],
        ["--timeout", "0"],
    ],
)
def test_arguments_are_checked_before_anything_is_sent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    class Silent:
        def request(self, *args: Any, **kwargs: Any) -> script.Reply:
            pytest.fail("Something was sent")

    with pytest.raises(SystemExit) as stop:
        run(capsys, Silent(), tmp_path / "out", *argv)
    assert stop.value.code == 2


def test_the_targets_use_the_probes_syntax(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Silent:
        def request(self, *args: Any, **kwargs: Any) -> script.Reply:
            pytest.fail("Something was sent")

    for bad in (["Amount"], ["Amount:float"], ["Pick:one_of"], ["A:text", "a:text"]):
        argv = ["--eml-dir", str(FIXTURES), "--task", TASK, "--allow-network"]
        for spec in bad:
            argv += ["--target", spec]
        with pytest.raises(SystemExit) as stop:
            script.main(argv, Silent())
        assert stop.value.code == 2
    assert script.target_body(script.parse_target("Event:one_of:a|b")) == {
        "column": "Event",
        "kind": "one_of",
        "options": ["a", "b"],
    }


# --- Refusals and failures of the API -------------------------------------------------


def test_an_api_refusal_prints_its_code_and_never_the_typed_column_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--eml-dir", str(FIXTURES), "--task", TASK, "--allow-network"]
    # "Received" is a date column of the table: a number cannot be read into it.
    argv += ["--target", "Received:number", "--out", str(tmp_path / "out")]
    with application(tmp_path, monkeypatch) as (transport, _, log):
        code = script.main(argv, transport, pause=lambda seconds: None)
        shown = capsys.readouterr()
    assert code == script.EXIT_FAILED
    assert "extraction_plan_invalid" in shown.err
    assert "Received" not in shown.out + shown.err
    assert not log.exists() and not (tmp_path / "out").exists()


def test_a_refusal_with_a_fixed_message_prints_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with application(tmp_path, monkeypatch) as (transport, _, _):
        # The server refuses the run: no consent in the request (the live gate, here forced).
        class Refusing(ClientTransport):
            def request(self, method: str, path: str, *args: Any, **kwargs: Any) -> script.Reply:
                if path.endswith("/runs"):
                    return script.Reply(
                        403,
                        b'{"error": {"code": "network_disabled", "message": "Enable network."}}',
                    )
                return super().request(method, path, *args, **kwargs)

        code, shown = run(capsys, Refusing(transport.client), tmp_path / "out")
    assert code == script.EXIT_FAILED
    assert "network_disabled: Enable network." in shown


class Scripted:
    """Canned answers by route, for what a real application does not do on demand."""

    def __init__(self, routes: dict[str, script.Reply | Exception]) -> None:
        self.routes = routes
        self.sent: list[str] = []

    def request(
        self, method: str, path: str, body: bytes | None = None, content_type: str | None = None
    ) -> script.Reply:
        self.sent.append(f"{method} {path.split('?')[0]}")
        answer: script.Reply | Exception | None = self.routes.get(f"{method} {path.split('?')[0]}")
        if isinstance(answer, Exception):
            raise answer
        return answer or script.Reply(404, b"{}")


def json_reply(status: int, body: Any) -> script.Reply:
    return script.Reply(status, json.dumps(body).encode())


LIMITS = {
    "max_run_rows": 20,
    "max_timeout_seconds": 300,
    "mail": True,
    "text_extraction": True,
}


@pytest.mark.parametrize(
    "limits",
    [
        script.Reply(200, b"<html>Another server</html>"),
        script.Reply(200, b"[1, 2]"),
        json_reply(200, {"status": "ok"}),
        json_reply(200, {**LIMITS, "max_run_rows": "20"}),
        json_reply(200, {**LIMITS, "mail": 1}),
    ],
)
def test_a_server_that_is_not_this_application_gets_one_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], limits: script.Reply
) -> None:
    transport = Scripted({"GET /api/limits": limits})
    code, shown = run(capsys, transport, tmp_path / "out")
    assert code == script.EXIT_FAILED and shown.strip() == script.NOT_THIS_APP
    assert transport.sent == ["GET /api/limits"]


def test_connection_refused_and_unexpected_statuses_get_one_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, shown = run(capsys, Scripted({"GET /api/limits": script.Unreachable()}), tmp_path / "o")
    assert code == script.EXIT_FAILED and shown.strip() == script.UNREACHABLE
    code, shown = run(
        capsys, Scripted({"GET /api/limits": script.Reply(502, b"bad gateway")}), tmp_path / "o"
    )
    assert code == script.EXIT_FAILED
    assert shown.strip() == "The server answered with an unexpected status (502)."
    # A refusal in the application's own shape but with an odd code is not trusted.
    odd = json_reply(500, {"error": {"code": "Boom <b>", "message": "x"}})
    code, shown = run(capsys, Scripted({"GET /api/limits": odd}), tmp_path / "o")
    assert shown.strip() == "The server answered with an unexpected status (500)."
    assert not (tmp_path / "o").exists()


def started(job: dict[str, Any]) -> Scripted:
    upload = {"dataset_id": "d1", "mail": {"read": 10, "skipped": 0, "truncated": 0, "masked": {}}}
    draft = {"plan_id": "p1", "fingerprint": "f" * 64, "validation": {"valid": True}}
    return Scripted(
        {
            "GET /api/limits": json_reply(200, LIMITS),
            "POST /api/mail-datasets": json_reply(201, upload),
            "POST /api/extraction-plans": json_reply(201, draft),
            "POST /api/plans/p1/approve": json_reply(200, {}),
            "POST /api/plans/p1/runs": json_reply(202, {"job_id": "abc123"}),
            "GET /api/jobs/abc123": json_reply(200, job),
            "POST /api/jobs/abc123/cancel": json_reply(202, {}),
        }
    )


def test_a_failed_job_exits_non_zero_with_its_code_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    error = {"code": "worker_failed", "message": "The run could not finish."}
    transport = started({"status": "failed", "error": error, "progress": None})
    code, shown = run(capsys, transport, tmp_path / "out")
    assert code == script.EXIT_FAILED
    assert "The run ended failed: worker_failed: The run could not finish." in shown
    assert not (tmp_path / "out").exists()
    assert not any("artifacts" in sent for sent in transport.sent)


def test_a_wait_past_the_timeout_asks_the_run_to_cancel(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = started({"status": "running", "progress": {"rows_done": 1, "rows_total": 10}})
    ticks = iter(range(0, 10_000, 40))
    argv = ["--eml-dir", str(FIXTURES), "--task", TASK, *TARGETS, "--allow-network"]
    argv += ["--out", str(tmp_path / "out"), "--timeout", "100"]
    code = script.main(argv, transport, clock=lambda: float(next(ticks)), pause=lambda s: None)
    shown = capsys.readouterr()
    assert code == script.EXIT_FAILED and "--timeout passed" in shown.err
    assert "POST /api/jobs/abc123/cancel" in transport.sent
    assert "Rows 1/10" in shown.out


def test_the_default_wait_is_the_servers_longest_run_plus_a_margin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = started({"status": "running", "progress": None})
    clock_values = iter([0.0, 0.0, 329.0, 331.0, 331.0])
    argv = ["--eml-dir", str(FIXTURES), "--task", TASK, *TARGETS, "--allow-network"]
    argv += ["--out", str(tmp_path / "out")]
    code = script.main(argv, transport, clock=lambda: next(clock_values), pause=lambda s: None)
    assert code == script.EXIT_FAILED
    assert "POST /api/jobs/abc123/cancel" in transport.sent
    assert "waiting for the worker" in capsys.readouterr().out


class StubOpener:
    def __init__(self, outcome: Exception | None, status: int = 200, body: bytes = b"{}") -> None:
        self.outcome, self.status, self.body = outcome, status, body
        self.seen: list[urllib.request.Request] = []

    def open(self, call: urllib.request.Request, timeout: float) -> Any:
        self.seen.append(call)
        if self.outcome:
            raise self.outcome
        return _Answer(self.status, self.body)


class _Answer:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self.body = status, body

    def __enter__(self) -> "_Answer":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


def test_the_real_transport_maps_what_urllib_does_onto_replies_and_one_exception() -> None:
    transport = script.UrllibTransport("http://127.0.0.1:8010/")
    opener = StubOpener(None, 201, b'{"a": 1}')
    transport._opener = opener  # type: ignore[assignment]
    assert transport.request("POST", "/api/x", b"data", "application/zip") == script.Reply(
        201, b'{"a": 1}'
    )
    [seen] = opener.seen
    assert seen.full_url == "http://127.0.0.1:8010/api/x" and seen.get_method() == "POST"
    assert seen.get_header("Content-type") == "application/zip"
    # A refusal is an answer; no answer at all is Unreachable.
    refused = urllib.error.HTTPError("u", 403, "no", {}, BytesIO(b'{"error": 1}'))  # type: ignore[arg-type]
    transport._opener = StubOpener(refused)  # type: ignore[assignment]
    assert transport.request("GET", "/api/x") == script.Reply(403, b'{"error": 1}')
    for failure in (urllib.error.URLError("refused"), ConnectionResetError(), TimeoutError()):
        transport._opener = StubOpener(failure)  # type: ignore[assignment]
        with pytest.raises(script.Unreachable):
            transport.request("GET", "/api/x")
    # A reply larger than the bound is not read to the end.
    monkey_big = StubOpener(None, 200, b"x" * (script.MAX_REPLY_BYTES + 5))
    transport._opener = monkey_big  # type: ignore[assignment]
    with pytest.raises(script.Unreachable):
        transport.request("GET", "/api/x")
