"""Acceptance run: build a register from a folder of .eml files through the running local app.

Unlike ``scripts/experiment_mail_extraction.py`` (the probe, which talks to the model itself),
this script only drives the application's public API: it uploads the mails
(``POST /api/mail-datasets``), has the server build the extraction plan, approves it, starts the
run, waits for it and downloads the result workbook and the report. It is for a person who wants
to accept the extraction on real mail before there is an interface. See
docs/32-mail-extraction-probe.md, "Acceptance run through the application".

Start the local application first (``python -m ai_data_enrichment.local_server --allow-network``
with ``ENRICHMENT_ALLOW_MAIL=1`` and an OpenAI key in the environment), then, from the
repository root:

    uv run --frozen python -m scripts.mail_register_via_app \\
        --eml-dir artifacts/mail-extraction/mails --allow-network \\
        --task "Payments: what was paid, to whom and when" \\
        --target "Service:text" --target "Amount:number" --target "Paid on:date"

``--allow-network`` is the per-run consent: the cleaned and masked text of every mail is sent to
OpenAI by the application. Nothing is sent to the application before the server's ``/api/limits``
say that the run can be made.

The script never prints mail content: no text, subject, sender, file name, quote or value. It
prints counts, codes, statuses, the paths of the files it wrote and fixed sentences. The two
files it downloads (``register.xlsx``, ``report.json``) do hold quotes from the mails.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Protocol
from urllib.parse import urlsplit
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from scripts.experiment_mail_extraction import ignored_by_git, inside, parse_target

from ai_data_enrichment.providers.text_extraction import ExtractionColumn, ExtractionTask

REPO_ROOT = Path(__file__).resolve().parents[1]
# The address `ai_data_enrichment.local_server` serves on (src/ai_data_enrichment/local_server.py).
DEFAULT_URL = "http://127.0.0.1:8010"
DEFAULT_OUT = Path("artifacts/mail-extraction-app")
# The column of the table the application builds from mail that holds the text of a mail.
TEXT_COLUMN = "Text"
# Extra seconds on top of the server's longest run: the upload, the plan and the downloads.
WAIT_MARGIN_SECONDS = 30
POLL_SECONDS = 1.0
# One HTTP call, not the whole run: an upload of many mails can take a while.
REQUEST_TIMEOUT_SECONDS = 120
MAX_REPLY_BYTES = 64 * 1024 * 1024
TERMINAL = {"completed", "needs_review", "failed", "cancelled", "timed_out"}
PUBLISHED = {"completed", "needs_review"}
# The only route whose message is made of what the person typed (the names of the columns).
CODE_ONLY = {"extraction_plan_invalid"}
LOOPBACK = {"127.0.0.1", "::1", "localhost"}

EXIT_FAILED = 1
EXIT_NOT_SENT = 2  # a refusal before anything was uploaded

NOT_THIS_APP = "The server at this address is not this application."
UNREACHABLE = (
    "No answer from the application at this address: it is not running, the address or port "
    "is wrong, or the connection was lost. Start it with "
    "python -m ai_data_enrichment.local_server --allow-network."
)
UNEXPECTED_SHAPE = "The application answered in a shape this script does not know."


class Stop(Exception):
    """End the run with one fixed sentence and an exit code; never carries mail content."""

    def __init__(self, message: str, code: int = EXIT_FAILED) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# --- Transport -----------------------------------------------------------------------


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes


class Unreachable(Exception):
    """No HTTP answer at all: refused, reset, timed out, or the server went away."""


class Transport(Protocol):
    def request(
        self, method: str, path: str, body: bytes | None = None, content_type: str | None = None
    ) -> Reply: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class UrllibTransport:
    """The real transport: standard library only, no proxy, no redirects."""

    def __init__(self, base_url: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # A local application must be reached directly, whatever HTTP(S)_PROXY says.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    def request(
        self, method: str, path: str, body: bytes | None = None, content_type: str | None = None
    ) -> Reply:
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        call = urllib.request.Request(  # noqa: S310 - scheme and host are checked by the caller
            self.base_url + path, data=body, method=method, headers=headers
        )
        try:
            with self._opener.open(call, timeout=self.timeout) as answer:
                return Reply(answer.status, self._read(answer))
        except urllib.error.HTTPError as error:
            with error:
                return Reply(error.code, self._read(error))
        except (OSError, ValueError, TimeoutError) as error:
            # URLError, ConnectionError, http.client errors and timeouts are all OSErrors.
            raise Unreachable from error

    @staticmethod
    def _read(stream: Any) -> bytes:
        data: bytes = stream.read(MAX_REPLY_BYTES + 1)
        if len(data) > MAX_REPLY_BYTES:
            raise Unreachable
        return data


def check_url(url: str) -> str | None:
    """A reason to refuse the address: the mails are for the local application only."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return "--url is not a valid address."
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        return "--url must be an http address without a user name, like http://127.0.0.1:8010."
    if host not in LOOPBACK:
        return "--url must be a loopback address: real mail is only sent to the local application."
    return None


# --- Calls ---------------------------------------------------------------------------


def call(
    transport: Transport,
    method: str,
    path: str,
    body: dict[str, Any] | bytes | None = None,
    *,
    expect: int = 200,
) -> dict[str, Any]:
    """One JSON call. Anything but ``expect`` ends the run with a fixed sentence."""
    if isinstance(body, dict):
        data, kind = json.dumps(body).encode(), "application/json"
    else:
        data, kind = body, "application/octet-stream" if body is not None else None
    try:
        reply = transport.request(method, path, data, kind)
    except Unreachable:
        raise Stop(UNREACHABLE) from None
    parsed = _json(reply.body)
    if reply.status == expect:
        if not isinstance(parsed, dict):
            raise Stop(NOT_THIS_APP)
        return parsed
    refusal = _refusal(parsed)
    if refusal is None:
        raise Stop(f"The server answered with an unexpected status ({reply.status}).")
    code, message = refusal
    # A message is a fixed sentence of the application, except where it names typed columns.
    shown = f"{code}: {message}" if code not in CODE_ONLY and message else code
    raise Stop(f"The application refused the request ({reply.status}): {shown}")


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        return None


def _refusal(parsed: Any) -> tuple[str, str] | None:
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(error, dict):
        return None
    code, message = error.get("code"), error.get("message")
    if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z_]{0,63}", code):
        return None
    ok = isinstance(message, str) and len(message) <= 300 and message.isprintable()
    return code, message if ok and isinstance(message, str) else ""


def whole(value: Any) -> int:
    """A whole number from a reply, else 0 (a bool is not a number here)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def numbers(value: Any) -> dict[str, Any]:
    """The whole numbers of a mapping under plain word keys, nested one level at most."""
    if not isinstance(value, dict):
        return {}
    kept: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_]{1,40}", key):
            continue
        kept[key] = numbers(item) if isinstance(item, dict) else whole(item)
    return kept


# --- Limits --------------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    max_run_rows: int
    max_timeout_seconds: int
    mail: bool
    text_extraction: bool


def read_limits(transport: Transport) -> Limits:
    body = call(transport, "GET", "/api/limits")
    try:
        rows, seconds = body["max_run_rows"], body["max_timeout_seconds"]
        mail, extraction = body["mail"], body["text_extraction"]
    except KeyError:
        raise Stop(NOT_THIS_APP) from None
    if (
        not all(isinstance(n, int) and not isinstance(n, bool) for n in (rows, seconds))
        or not isinstance(mail, bool)
        or not isinstance(extraction, bool)
    ):
        raise Stop(NOT_THIS_APP)
    return Limits(rows, seconds, mail, extraction)


def refuse_by_limits(transport: Transport, limits: Limits, mails: int) -> None:
    """Stop, before anything is uploaded, when the server could not run this."""
    if not limits.mail:
        raise Stop(
            "Tables from e-mails are not offered by this server. Set ENRICHMENT_ALLOW_MAIL=1 "
            "in the environment of the local launcher (python -m "
            "ai_data_enrichment.local_server) and restart it.",
            EXIT_NOT_SENT,
        )
    if not limits.text_extraction:
        raise Stop(_why_not_extracting(transport), EXIT_NOT_SENT)
    if mails > limits.max_run_rows:
        raise Stop(
            f"The server runs at most {limits.max_run_rows} mails at a time and there are "
            f"{mails}. Set ENRICHMENT_MAX_RUN_ROWS to at least {mails} and restart the "
            "application, or use --limit.",
            EXIT_NOT_SENT,
        )


def _why_not_extracting(transport: Transport) -> str:
    """Name the setting: read the open provider status, else name every candidate."""
    key = gate = None
    try:
        status = call(transport, "GET", "/api/provider-settings")
        key, gate = status.get("key_present"), status.get("server_network_allowed")
    except Stop:
        pass
    if key is False:
        return "The server has no OpenAI key: set OPENAI_API_KEY in its environment (.env)."
    if gate is False:
        return (
            "The server's network gate is closed: start it with --allow-network or set "
            "ENRICHMENT_ALLOW_NETWORK=1."
        )
    return (
        "Reading values out of mail is not offered by this server. It needs an OpenAI key "
        "(OPENAI_API_KEY) and the network gate (--allow-network or ENRICHMENT_ALLOW_NETWORK=1)."
    )


# --- Mails and the upload ------------------------------------------------------------


def collect_mails(folder: Path, limit: int | None) -> list[bytes]:
    """The first ``limit`` ``*.eml`` files of the folder in file name order (all when None).

    This is not the probe's choice: the probe orders by the ``Date`` header before it applies
    ``--limit``. Here the order is the names', so that no mail has to be parsed by the script.
    """
    files = sorted(p for p in folder.glob("*.eml") if p.is_file())
    return [path.read_bytes() for path in files[:limit]]


def neutral_zip(mails: Sequence[bytes]) -> bytes:
    """The mails as a stored zip with entry names ``0001.eml``..., so no file name is sent.

    The server ignores entry names (it numbers the entries itself, ``mail/upload.py``); the
    fixed time keeps the archive from carrying the modification times of the files.
    """
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_STORED) as archive:
        for number, raw in enumerate(mails, 1):
            entry = ZipInfo(f"{number:04d}.eml", date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_STORED
            archive.writestr(entry, raw)
    return buffer.getvalue()


def target_body(column: ExtractionColumn) -> dict[str, Any]:
    body: dict[str, Any] = {"column": column.header, "kind": column.kind}
    if column.kind == "one_of":
        body["options"] = list(column.options)
    return body


# --- The run -------------------------------------------------------------------------


class Progress:
    """One line of numbers. On a terminal it is rewritten; otherwise a line per change."""

    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.tty = bool(getattr(stream, "isatty", lambda: False)())
        self.last = ""

    def show(self, job: dict[str, Any]) -> None:
        progress = job.get("progress")
        if not isinstance(progress, dict):
            line = f"Run {job.get('status')}: waiting for the worker"
        else:
            steps = numbers(progress.get("steps"))
            tally = {
                kind: sum(
                    whole(step.get(kind)) for step in steps.values() if isinstance(step, dict)
                )
                for kind in ("filled", "failed", "skipped", "not_applicable")
            }
            line = (
                f"Rows {whole(progress.get('rows_done'))}/{whole(progress.get('rows_total'))}"
                f" | filled {tally['filled']}, not applicable {tally['not_applicable']},"
                f" failed {tally['failed']}, skipped {tally['skipped']}"
            )
        if line == self.last:
            return
        self.last = line
        self.stream.write(f"\r{line:<79}" if self.tty else f"{line}\n")
        self.stream.flush()

    def end(self) -> None:
        if self.tty and self.last:
            self.stream.write("\n")
            self.stream.flush()


def wait_for_job(
    transport: Transport,
    job_id: str,
    *,
    deadline: float,
    progress: Progress,
    clock: Callable[[], float],
    pause: Callable[[float], None],
) -> dict[str, Any]:
    while True:
        job = call(transport, "GET", f"/api/jobs/{job_id}")
        progress.show(job)
        if job.get("status") in TERMINAL:
            progress.end()
            return job
        if clock() >= deadline:
            progress.end()
            try:  # best effort: the run should not go on spending requests unattended
                transport.request("POST", f"/api/jobs/{job_id}/cancel")
            except Unreachable:
                pass
            raise Stop("Stopped waiting: --timeout passed. The run was asked to cancel.")
        pause(POLL_SECONDS)


def download(transport: Transport, path: Any) -> bytes:
    if not isinstance(path, str) or not path.startswith("/api/jobs/"):
        raise Stop(UNEXPECTED_SHAPE)
    try:
        reply = transport.request("GET", path)
    except Unreachable:
        raise Stop(UNREACHABLE) from None
    if reply.status != 200:
        raise Stop(f"The server answered with an unexpected status ({reply.status}).")
    return reply.body


def write_private(path: Path, data: bytes) -> None:
    """Mail-derived files: readable by the owner only where the system has modes."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def build_summary(
    upload: dict[str, Any], job: dict[str, Any], report: dict[str, Any], seconds: float
) -> dict[str, Any]:
    """Numbers and fixed words only; every mapping is filtered to word keys and whole numbers."""
    extraction = report.get("text_extraction")
    if not isinstance(extraction, dict):
        raise Stop(UNEXPECTED_SHAPE)
    mail = numbers(upload.get("mail"))
    return {
        "job_status": str(job.get("status")),
        "mails": {
            "read": whole(mail.get("read")),
            "skipped": whole(mail.get("skipped")),
            "truncated": whole(mail.get("truncated")),
            "masked": numbers(mail.get("masked")),
        },
        "rows": numbers(extraction.get("rows")),
        "values_by_basis": numbers(extraction.get("values_kept")),
        "values_dropped_by_reason": numbers(extraction.get("values_dropped")),
        "requests": whole(extraction.get("requests")),
        "input_tokens": whole(extraction.get("input_tokens")),
        "output_tokens": whole(extraction.get("output_tokens")),
        "answers_kept": whole(extraction.get("answers_kept")),
        "elapsed_seconds": round(seconds, 1),
    }


def print_summary(summary: dict[str, Any], out: Path) -> None:
    def pairs(counts: dict[str, Any]) -> str:
        return ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in counts.items()) or "none"

    mails = summary["mails"]
    print(f"Run {summary['job_status']} in {summary['elapsed_seconds']} s.")
    print(
        f"Mails: {mails['read']} read, {mails['skipped']} skipped, {mails['truncated']} cut; "
        f"masked: {pairs(mails['masked'])}."
    )
    print(f"Rows: {pairs(summary['rows'])}.")
    print(
        f"Values: {pairs(summary['values_by_basis'])}; "
        f"dropped: {pairs(summary['values_dropped_by_reason'])}."
    )
    print(
        f"Requests: {summary['requests']} ({summary['input_tokens']} input and "
        f"{summary['output_tokens']} output tokens); {summary['answers_kept']} answers reused."
    )
    print(f"Files: {out.resolve()}")
    print("register.xlsx and report.json hold quotes from the mails; do not commit or share them.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.mail_register_via_app",
        description="Build a register from .eml files through the running local application.",
        epilog='Each --target is "Header:kind" (text, number, date, yes_no) or '
        '"Header:one_of:a|b|c", as in the probe.',
    )
    parser.add_argument("--eml-dir", type=Path, required=True, help="Folder of .eml files")
    parser.add_argument("--task", required=True, help="What the mails are asked about")
    parser.add_argument("--target", action="append", required=True, help="A column to fill")
    parser.add_argument(
        "--url", default=DEFAULT_URL, help=f"The application (default {DEFAULT_URL})"
    )
    parser.add_argument(
        "--out",
        type=Path,
        help=f"Output folder (default {DEFAULT_OUT}/<UTC timestamp>/); refused inside the "
        "repository unless git ignores it",
    )
    parser.add_argument(
        "--limit", type=int, help="Take only the first N files in file name order (default all)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Seconds to wait for the whole run (default: the server's longest run plus "
        f"{WAIT_MARGIN_SECONDS})",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="Consent of this run: the application sends the cleaned text of every mail to OpenAI",
    )
    return parser


def execute(
    args: argparse.Namespace,
    transport: Transport,
    task: ExtractionTask,
    mails: list[bytes],
    out: Path,
    *,
    clock: Callable[[], float],
    pause: Callable[[float], None],
) -> int:
    started = clock()
    limits = read_limits(transport)
    refuse_by_limits(transport, limits, len(mails))
    wait = (
        args.timeout
        if args.timeout is not None
        else limits.max_timeout_seconds + WAIT_MARGIN_SECONDS
    )
    print(
        f"Sending {len(mails)} mails to the application; it will make up to {len(mails)} "
        "requests to OpenAI with their cleaned text."
    )

    upload = call(
        transport,
        "POST",
        "/api/mail-datasets?filename=mails.zip",
        neutral_zip(mails),
        expect=201,
    )
    dataset_id = upload.get("dataset_id")
    if not isinstance(dataset_id, str):
        raise Stop(UNEXPECTED_SHAPE)
    counts = numbers(upload.get("mail"))
    print(f"Table built: {whole(counts.get('read'))} read, {whole(counts.get('skipped'))} skipped.")

    draft = call(
        transport,
        "POST",
        "/api/extraction-plans",
        {
            "dataset_id": dataset_id,
            "question": task.question,
            "text_column": TEXT_COLUMN,
            "targets": [target_body(column) for column in task.columns],
        },
        expect=201,
    )
    plan_id, fingerprint = draft.get("plan_id"), draft.get("fingerprint")
    validation = draft.get("validation")
    if not isinstance(plan_id, str) or not isinstance(fingerprint, str):
        raise Stop(UNEXPECTED_SHAPE)
    if not isinstance(validation, dict) or validation.get("valid") is not True:
        raise Stop("The application did not accept the plan as valid; nothing was run.")
    call(
        transport,
        "POST",
        f"/api/plans/{plan_id}/approve",
        {"approved": True, "fingerprint": fingerprint},
    )

    # No timeout_seconds: an extraction run then gets the server's longest limit.
    run = call(
        transport,
        "POST",
        f"/api/plans/{plan_id}/runs",
        {"allow_network": args.allow_network},
        expect=202,
    )
    job_id = run.get("job_id")
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{1,64}", job_id):
        raise Stop(UNEXPECTED_SHAPE)
    job = wait_for_job(
        transport,
        job_id,
        deadline=started + wait,
        progress=Progress(sys.stdout),
        clock=clock,
        pause=pause,
    )

    status = job.get("status")
    if status not in PUBLISHED:
        error = job.get("error")
        detail = ""
        if isinstance(error, dict) and (shown := _refusal({"error": error})):
            detail = f": {shown[0]}: {shown[1]}" if shown[1] else f": {shown[0]}"
        raise Stop(f"The run ended {status}{detail}")

    artifacts = job.get("artifacts")
    if not isinstance(artifacts, dict):
        raise Stop(UNEXPECTED_SHAPE)
    result = download(transport, artifacts.get("result"))
    report_bytes = download(transport, artifacts.get("report"))
    report = _json(report_bytes)
    if not isinstance(report, dict):
        raise Stop(UNEXPECTED_SHAPE)
    summary = build_summary(upload, job, report, clock() - started)

    out.mkdir(parents=True, exist_ok=True)
    write_private(out / "register.xlsx", result)
    write_private(out / "report.json", report_bytes)
    write_private(out / "summary.json", (json.dumps(summary, indent=2) + "\n").encode())
    print_summary(summary, out)
    return 0


def main(
    argv: Sequence[str] | None = None,
    transport: Transport | None = None,
    *,
    clock: Callable[[], float] = monotonic,
    pause: Callable[[float], None] = sleep,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.timeout is not None and args.timeout <= 0:
        parser.error("--timeout must be positive")
    if reason := check_url(args.url):
        parser.error(reason)
    try:
        task = ExtractionTask(args.task, tuple(parse_target(spec) for spec in args.target))
    except ValueError as error:
        parser.error(str(error))
    if not args.eml_dir.is_dir():
        parser.error("--eml-dir is not a folder.")
    out: Path = args.out or DEFAULT_OUT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if inside(out, REPO_ROOT) and not ignored_by_git(out):
        parser.error(
            "--out is inside the repository and git does not ignore it: the files hold quotes "
            "from real mail. Use a folder under artifacts/ or outside the repository."
        )
    if not args.allow_network:
        print(
            "Add --allow-network to confirm that the application sends the cleaned text of "
            "the mails to OpenAI. Nothing was sent.",
            file=sys.stderr,
        )
        return EXIT_NOT_SENT
    mails = collect_mails(args.eml_dir, args.limit)
    if not mails:
        print("No .eml files in the folder. Nothing was sent.", file=sys.stderr)
        return EXIT_NOT_SENT

    try:
        return execute(
            args,
            transport or UrllibTransport(args.url),
            task,
            mails,
            out,
            clock=clock,
            pause=pause,
        )
    except Stop as stop:
        print(stop.message, file=sys.stderr)
        return stop.code


if __name__ == "__main__":
    sys.exit(main())
