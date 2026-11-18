"""Probe: can a model fill a table from e-mails, with a quote for every value?

This is a measurement script, not part of the application. It reads a folder of ``.eml``
files, or the person's own Gmail mailbox (read-only), cleans and masks the text of every
message, asks the model once per message to fill the target columns from that text, and
writes what came back to a workbook and a JSON file. It never writes into the folder it
reads. See docs/32-mail-extraction-probe.md.

Run it as a module from the repository root:

    uv run --env-file .env python -m scripts.experiment_mail_extraction \\
        --eml-dir samples/probe/mail \\
        --task "Payments and subscriptions: what was paid, or what changes for a subscription" \\
        --target "Service:text" --target "Amount:number" --target "Paid on:date" \\
        --target "Event:one_of:payment|renewal notice|trial ending|ended"

--dry-run prints the exact request for the first message and what masking replaced, and
sends nothing to the model. With --gmail-query it still READS the messages from Gmail (it
has to, to show one); only the request to OpenAI is left out.

Gmail instead of files (needs the optional extra: uv sync --extra mail, one sign-in in the
browser, a client file from your own Google Cloud project):

    uv run --extra mail --env-file .env python -m scripts.experiment_mail_extraction \\
        --gmail-query "category:purchases newer_than:180d" --limit 40 \\
        --gmail-client ~/secrets/client_secret.json --task ... --target ...

Privacy: the cleaned and masked text of every message (plus its subject, sender and date)
is sent to the model provider, and the answers with their quotes are written to
artifacts/mail-extraction/ (ignored by git). Masking covers card tails, phone numbers
that start with + and the mailbox owner's address; it does not cover names or postal
addresses. Do not point the script at mail that must not leave your machine. The API key
is read from OPENAI_API_KEY and is never written anywhere.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.comments import Comment
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from scripts.experiment_web_lookup import (
    DEFAULT_PRICE_INPUT_PER_MTOK,
    DEFAULT_PRICE_OUTPUT_PER_MTOK,
    Prices,
    RecordingClient,
    estimate_cost,
    plural,
)

from ai_data_enrichment.mail import gmail
from ai_data_enrichment.mail.cleaning import DEFAULT_MAX_CHARS
from ai_data_enrichment.mail.masking import KINDS as MASK_KINDS
from ai_data_enrichment.mail.masking import mask_text
from ai_data_enrichment.mail.messages import MailMessage, read_eml_folder_report, sort_messages
from ai_data_enrichment.providers.text_extraction import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    KINDS,
    PROMPT_VERSION,
    ExtractionColumn,
    ExtractionTask,
    ExtractionUsage,
    build_request,
    configuration_id,
)
from ai_data_enrichment.providers.text_extraction_handler import TextExtractionHandler
from ai_data_enrichment.providers.text_extraction_reply import (
    OUTCOME_FOUND,
    ExtractionResult,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path("artifacts/mail-extraction")
DEFAULT_GMAIL_LIMIT = 40
# An error code after which every later message would fail the same way.
STOP_CODES = ("SOURCE_ACCESS_DENIED", "REQUEST_REFUSED")
MAX_TARGETS = 20


# --- Targets -------------------------------------------------------------------------


def parse_target(spec: str) -> ExtractionColumn:
    """Read "Header:kind" or "Header:one_of:a|b|c"; raises ValueError with a clear message.

    The spec is split from the right, so a header may hold a colon.
    """
    match = re.fullmatch(r"(?P<header>.*):one_of:(?P<options>.*)", spec, re.DOTALL)
    try:
        if match:
            options = tuple(o.strip() for o in match["options"].split("|"))
            return ExtractionColumn(match["header"], "one_of", options)
        header, _, kind = spec.rpartition(":")
        kind = kind.strip()
        if not header.strip() or kind not in KINDS:
            raise ValueError(
                f"expected Header:kind or Header:one_of:a|b|c with kind one of {', '.join(KINDS)}"
            )
        if kind == "one_of":
            raise ValueError("one_of needs its options: Header:one_of:a|b|c")
        return ExtractionColumn(header, kind)
    except ValueError as exc:
        raise ValueError(f"--target {spec!r}: {exc}") from exc


# --- What is sent --------------------------------------------------------------------


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
    document = message_document(message)
    if not mask:
        return document, dict.fromkeys(MASK_KINDS, 0)
    return mask_text(document, own_addresses=own_addresses(messages, message, known))


# --- Output files --------------------------------------------------------------------


def from_text(message: MailMessage) -> str:
    if message.sender_name:
        return message.sender_name
    return message.sender_address.rpartition("@")[2] or message.sender_address


def json_row(
    message: MailMessage, result: ExtractionResult, masking: dict[str, int], seconds: float
) -> dict[str, Any]:
    """One message as written to rows.json: facts and answers, never the message text."""
    return {
        "source": message.source,
        "message_id": message.message_id,
        "received": message.received.isoformat() if message.received else None,
        "sender_name": message.sender_name,
        "sender_address": message.sender_address,
        "subject": message.subject,
        "outcome": result.outcome,
        "message": result.message,
        "values": {
            v.column: {"value": v.value, "mark": v.mark, "quote": v.quote} for v in result.kept
        },
        "dropped": [
            {"column": d.column, "reason": d.reason, "value": d.value, "quote": d.quote}
            for d in result.dropped
        ],
        "masking": masking,
        "truncated": message.truncated,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "seconds": seconds,
    }


def _text(sheet: Worksheet, row: int, column: int, value: str) -> Any:
    """A text cell; a text that starts with = + - @ stays text and is never a formula."""
    cell = sheet.cell(row=row, column=column, value=ILLEGAL_CHARACTERS_RE.sub("", value))
    cell.data_type = "s"
    return cell


def _typed(sheet: Worksheet, row: int, column: int, kind: str, value: str | float | None) -> Any:
    if isinstance(value, str) and kind == "date":
        try:
            cell = sheet.cell(row=row, column=column, value=date.fromisoformat(value))
            cell.number_format = "yyyy-mm-dd"
            return cell
        except ValueError:
            pass
    if isinstance(value, float) or (isinstance(value, int) and not isinstance(value, bool)):
        number = int(value) if float(value).is_integer() else value
        return sheet.cell(row=row, column=column, value=number)
    return _text(sheet, row, column, "" if value is None else str(value))


def _header_row(sheet: Worksheet, headers: Sequence[str], widths: Sequence[int]) -> None:
    for index, header in enumerate(headers, start=1):
        sheet.cell(row=1, column=index, value=header).font = Font(bold=True)
        sheet.column_dimensions[get_column_letter(index)].width = widths[index - 1]
    sheet.freeze_panes = "A2"


def _note(mark: str, quote: str) -> Comment:
    note = Comment(ILLEGAL_CHARACTERS_RE.sub("", f"{mark}\n{quote}"), "mail probe")
    note.width, note.height = 360, 140
    return note


def write_workbook(
    path: Path, task: ExtractionTask, records: Sequence[tuple[MailMessage, ExtractionResult]]
) -> None:
    workbook = Workbook()
    register = workbook.active
    assert register is not None
    register.title = "Register"
    targets = [column.header for column in task.columns]
    _header_row(
        register, ["Received", "From", "Subject", *targets], [12, 24, 40, *[18] * len(targets)]
    )
    other = workbook.create_sheet("Other")
    _header_row(other, ["Received", "From", "Subject", "Outcome"], [12, 24, 40, 24])
    checks = workbook.create_sheet("Checks")
    _header_row(
        checks,
        ["Source file", "Column", "Value", "Mark or reason", "Quote"],
        [34, 18, 24, 22, 70],
    )

    def received_cell(sheet: Worksheet, row: int, message: MailMessage) -> None:
        if message.received is not None:
            cell = sheet.cell(row=row, column=1, value=message.received.date())
            cell.number_format = "yyyy-mm-dd"

    kinds = {column.header: column.kind for column in task.columns}
    for message, result in records:
        if result.outcome == OUTCOME_FOUND:
            row = register.max_row + 1
            received_cell(register, row, message)
            _text(register, row, 2, from_text(message))
            _text(register, row, 3, message.subject)
            for value in result.kept:
                cell = _typed(
                    register, row, 4 + targets.index(value.column), value.kind, value.value
                )
                cell.comment = _note(value.mark, value.quote)
        else:
            row = other.max_row + 1
            received_cell(other, row, message)
            _text(other, row, 2, from_text(message))
            _text(other, row, 3, message.subject)
            _text(other, row, 4, result.outcome)
        for value in result.kept:
            row = checks.max_row + 1
            _text(checks, row, 1, message.source)
            _text(checks, row, 2, value.column)
            _typed(checks, row, 3, value.kind, value.value)
            _text(checks, row, 4, value.mark)
            _text(checks, row, 5, value.quote)
        for dropped in result.dropped:
            row = checks.max_row + 1
            _text(checks, row, 1, message.source)
            _text(checks, row, 2, dropped.column)
            _typed(checks, row, 3, kinds[dropped.column], dropped.value)
            _text(checks, row, 4, dropped.reason)
            _text(checks, row, 5, dropped.quote or "")
    workbook.save(path)


# --- Summary -------------------------------------------------------------------------


def summarize(
    rows: Sequence[dict[str, Any]],
    *,
    read: int,
    skipped: Sequence[str],
    task: ExtractionTask,
    model: str,
    effort: str | None,
    args: argparse.Namespace,
    prices: Prices,
    stopped: str | None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_tokens = sum(r["input_tokens"] for r in rows)
    output_tokens = sum(r["output_tokens"] for r in rows)
    masking = Counter[str]()
    for row in rows:
        masking.update(row["masking"])
    return {
        "configuration_id": configuration_id(model, effort),
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "reasoning_effort": effort,
        "source": source
        or {
            "kind": "eml",
            "query": None,
            "listed": read + len(skipped),
            "fetched": read,
            "skipped": len(skipped),
            "saved_eml": False,
        },
        "masking": not args.no_mask,
        "max_chars": args.max_chars,
        "task": {
            "question": task.question,
            "targets": [
                {"header": c.header, "kind": c.kind, "options": list(c.options)}
                for c in task.columns
            ],
        },
        "messages": {
            "read": read,
            "skipped": len(skipped),
            "skipped_files": list(skipped),
            "asked": len(rows),
            "stopped_early": stopped,
        },
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "values_kept_by_mark": dict(Counter(v["mark"] for r in rows for v in r["values"].values())),
        "values_dropped_by_reason": dict(Counter(d["reason"] for r in rows for d in r["dropped"])),
        "masking_counts": {kind: masking.get(kind, 0) for kind in MASK_KINDS},
        "truncated_messages": sum(1 for r in rows if r["truncated"]),
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "seconds": round(sum(r["seconds"] for r in rows), 2),
        "estimated_cost": round(estimate_cost(0, input_tokens, output_tokens, prices), 4),
        "prices_used": {
            "input_per_mtok": prices.input_per_mtok,
            "output_per_mtok": prices.output_per_mtok,
        },
    }


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{name} {count}" for name, count in sorted(counts.items())) or "none"


def print_summary(summary: dict[str, Any]) -> None:
    messages = summary["messages"]
    usage = summary["usage"]
    prices = summary["prices_used"]
    source = summary["source"]
    print()
    print(f"Configuration: {summary['configuration_id']}")
    print(
        f"Source: {source['kind']}"
        + (f", query {source['query']!r}" if source["query"] else "")
        + f"; listed {source['listed']}, fetched {source['fetched']}, skipped {source['skipped']}"
    )
    print(
        f"Messages: {messages['read']} read, {messages['skipped']} skipped (not readable), "
        f"{messages['asked']} asked"
        + (f"; stopped early: {messages['stopped_early']}" if messages["stopped_early"] else "")
    )
    print(f"Outcomes: {_counts(summary['outcomes'])}")
    print(f"Values kept, by mark: {_counts(summary['values_kept_by_mark'])}")
    print(f"Values dropped, by reason: {_counts(summary['values_dropped_by_reason'])}")
    print(
        f"Masking: {_counts(summary['masking_counts'])}"
        + ("" if summary["masking"] else " (masking was off)")
    )
    print(f"Messages cut at {summary['max_chars']} characters: {summary['truncated_messages']}")
    print(f"Tokens in / out: {usage['input_tokens']} / {usage['output_tokens']}")
    print(f"Seconds: {summary['seconds']}")
    print(
        f"Estimated cost: {summary['estimated_cost']} USD at {prices['input_per_mtok']}/"
        f"{prices['output_per_mtok']} per 1M tokens in/out"
    )
    print("The cost is an estimate from the --price-* options; it was not compared with the bill.")


# --- Command line --------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--eml-dir", type=Path, help="Folder with .eml files (one of --eml-dir, --gmail-query)"
    )
    parser.add_argument(
        "--gmail-query",
        metavar="QUERY",
        help="Read your own Gmail mailbox, read-only, with this Gmail search, for example "
        '"category:purchases newer_than:180d" (needs the optional extra: uv sync --extra mail). '
        "Messages are read from Gmail even with --dry-run; only the requests to OpenAI "
        "are left out.",
    )
    parser.add_argument(
        "--gmail-client",
        type=Path,
        metavar="PATH",
        help="The OAuth client JSON (type Desktop app) from your own Google Cloud project; "
        f"or set {gmail.CLIENT_FILE_ENV}. Keep it outside the repository. Needed only to sign in.",
    )
    parser.add_argument(
        "--gmail-account",
        metavar="LABEL",
        help="Name of the stored sign-in on this machine, so two people can keep separate "
        f"tokens (letters, digits, - and _; default {gmail.DEFAULT_LABEL})",
    )
    parser.add_argument(
        "--gmail-sign-out",
        action="store_true",
        help="Delete the stored sign-in of --gmail-account and exit (it does not revoke the "
        "access at Google)",
    )
    parser.add_argument(
        "--save-eml",
        type=Path,
        metavar="DIR",
        help="With --gmail-query: also write every message read as <n>_<id>.eml into DIR, so "
        "that --eml-dir DIR repeats the run on the same mails. Real mail: refused inside the "
        "repository unless under artifacts/. Deleting the files is up to you.",
    )
    parser.add_argument("--task", help="What the table is about, in one sentence")
    parser.add_argument(
        "--target",
        action="append",
        metavar="HEADER:KIND",
        help="A column to fill (1 to 20, repeatable); kind is text, number, date or yes_no, "
        "or HEADER:one_of:a|b|c for a choice. A header may hold a colon.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only the first N messages (oldest first; with Gmail the newest N matching the "
        f"query, default {DEFAULT_GMAIL_LIMIT}, at most {gmail.MAX_LIMIT})",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning-effort", default=DEFAULT_EFFORT)
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help="Cut the body of a message here, on a line boundary (default %(default)s)",
    )
    parser.add_argument(
        "--no-mask",
        action="store_true",
        help="Send the text without masking card tails, phone numbers and your address",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help=f"Where to write rows.json and register.xlsx (default {DEFAULT_OUT}/<timestamp>)",
    )
    parser.add_argument("--timeout", type=float, default=90.0, help="Seconds per request")
    parser.add_argument("--price-input-per-mtok", type=float, default=DEFAULT_PRICE_INPUT_PER_MTOK)
    parser.add_argument(
        "--price-output-per-mtok", type=float, default=DEFAULT_PRICE_OUTPUT_PER_MTOK
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request for the first message and what masking replaced; send nothing "
        "to OpenAI (Gmail is still read)",
    )
    return parser


def inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def ignored_by_git(path: Path, root: Path = REPO_ROOT) -> bool:
    """Whether git ignores the path. False also when git cannot say (no git, not a clone)."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed command, the path is an argument
            ["git", "-C", str(root), "check-ignore", "-q", "--", str(path.resolve())],  # noqa: S607
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def check_save_dir(directory: Path, root: Path = REPO_ROOT) -> str | None:
    """A reason to refuse DIR for real mail: inside the repository, outside ``artifacts/``."""
    if inside(directory, root) and not inside(directory, root / "artifacts"):
        return (
            f"--save-eml {directory}: real mail must not be written inside the repository "
            "outside artifacts/ (ignored by git); use a folder under artifacts/ or outside"
        )
    return None


def save_eml(directory: Path, mails: Sequence[gmail.FetchedMail]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for mail in mails:
        target = directory / mail.message.source
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(mail.raw)


GmailOpener = Callable[[Path | None, str], gmail.GmailSource]


def run(
    argv: Sequence[str] | None = None,
    client: Any = None,
    gmail_opener: GmailOpener | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    label = args.gmail_account or gmail.DEFAULT_LABEL
    try:
        gmail.validate_label(label)
    except ValueError as exc:
        parser.error(str(exc))
    if args.gmail_sign_out:
        if args.eml_dir or args.gmail_query:
            parser.error("--gmail-sign-out is used alone")
        removed = gmail.sign_out(label)
        print(
            f"Deleted the stored Gmail sign-in '{label}'."
            if removed
            else f"No stored Gmail sign-in '{label}'."
        )
        print("Access is revoked in your Google Account, under third-party connections.")
        return 0

    if (args.eml_dir is None) == (args.gmail_query is None):
        parser.error("Give exactly one of --eml-dir PATH or --gmail-query QUERY")
    use_gmail = args.gmail_query is not None
    if use_gmail and not args.gmail_query.strip():
        parser.error("--gmail-query cannot be empty")
    if not use_gmail and (args.save_eml or args.gmail_client or args.gmail_account):
        parser.error("--save-eml, --gmail-client and --gmail-account go with --gmail-query")
    if args.task is None:
        parser.error("--task is required")
    if not args.task.strip():
        parser.error("--task cannot be empty")
    if not args.target:
        parser.error("At least one --target is required")
    if len(args.target) > MAX_TARGETS:
        parser.error(f"At most {MAX_TARGETS} --target options")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if use_gmail and args.limit is not None and args.limit > gmail.MAX_LIMIT:
        parser.error(f"--limit is at most {gmail.MAX_LIMIT} with --gmail-query")
    if args.max_chars < 200 or args.timeout <= 0:
        parser.error("--max-chars must be at least 200 and --timeout positive")
    if not use_gmail and not args.eml_dir.is_dir():
        parser.error(f"Folder not found: {args.eml_dir}")
    try:
        task = ExtractionTask(args.task, tuple(parse_target(spec) for spec in args.target))
    except ValueError as exc:
        parser.error(str(exc))

    api_key = os.getenv("OPENAI_API_KEY")
    source: dict[str, Any] | None = None
    known: set[str] = set()
    if use_gmail:
        if args.save_eml and (reason := check_save_dir(args.save_eml)):
            parser.error(reason)
        if not args.dry_run and client is None and not api_key:
            parser.error("Set OPENAI_API_KEY in the local shell or an ignored .env file")
        limit = args.limit or DEFAULT_GMAIL_LIMIT
        client_file = args.gmail_client or (
            Path(value) if (value := os.getenv(gmail.CLIENT_FILE_ENV)) else None
        )
        if client_file is not None:
            client_file = client_file.expanduser()
            if inside(client_file, REPO_ROOT) and not ignored_by_git(client_file):
                print(
                    f"Warning: {client_file} is inside the repository and git does not ignore "
                    "it; it must not be committed. Move it outside, or into the ignored "
                    "credentials/ folder."
                )
        backend: gmail.Backend | None = None
        if gmail_opener is None:
            try:
                backend = gmail.GoogleBackend()  # fails before anything else without the extra
            except gmail.MissingExtraError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        print(
            f"Reading up to {limit} messages matching {args.gmail_query!r} from Gmail, "
            "read-only; "
            + (
                "nothing will be sent to OpenAI (--dry-run)."
                if args.dry_run
                else "their cleaned and masked text will be sent to OpenAI."
            ),
            flush=True,
        )
        try:
            if gmail_opener is None:
                mailbox = gmail.open_source(client_file, label, backend=backend)
            else:
                mailbox = gmail_opener(client_file, label)
            address = mailbox.profile_address()
            ids = mailbox.list_ids(args.gmail_query, limit)
            print(f"Gmail lists {len(ids)} messages; reading them one by one.", flush=True)
            fetched = mailbox.fetch(ids, max_chars=args.max_chars)
        except gmail.GmailAuthError as exc:
            gmail.sign_out(label)
            print(f"{exc} The stored sign-in was deleted.", file=sys.stderr)
            return 1
        except gmail.GmailError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if address:
            known.add(address)
        messages = sort_messages([mail.message for mail in fetched.mails])
        skipped = list(fetched.skipped_ids)
        source = {
            "kind": "gmail",
            "query": args.gmail_query,
            "listed": len(ids),
            "fetched": len(messages),
            "skipped": len(skipped),
            "saved_eml": bool(args.save_eml),
        }
        if args.save_eml and fetched.mails:
            save_eml(args.save_eml, fetched.mails)
            print(
                f"Saved {len(fetched.mails)} real messages as .eml in {args.save_eml}; "
                "deleting them is up to you."
            )
        if not messages:
            print(f"No readable messages ({len(skipped)} skipped).", file=sys.stderr)
            return 1
    else:
        messages, skipped = read_eml_folder_report(args.eml_dir, max_chars=args.max_chars)
        if not messages:
            parser.error(f"No readable .eml files in {args.eml_dir} ({len(skipped)} skipped)")
    asked = messages[: args.limit] if args.limit else messages
    model = args.model
    effort = args.reasoning_effort or None
    prepared = [prepare(messages, message, not args.no_mask, known) for message in asked]

    if args.dry_run:
        document, counts = prepared[0]
        total = Counter[str]()
        for _, item in prepared:
            total.update(item)
        print(f"First of {len(asked)} messages ({asked[0].source}); nothing is sent.")
        if use_gmail:
            print("The messages were read from Gmail; nothing is sent to OpenAI (--dry-run).")
        print(
            f"Masking replaced in this message: {_counts(counts)}; "
            f"in all {len(asked)}: {_counts(dict(total))}"
            + ("" if not args.no_mask else " (masking is off)")
        )
        request = build_request(task, document, model=model, reasoning_effort=effort)
        print(json.dumps({**request, "timeout": args.timeout}, ensure_ascii=False, indent=2))
        return 0

    if client is None:
        if not api_key:
            parser.error("Set OPENAI_API_KEY in the local shell or an ignored .env file")
        from openai import OpenAI

        client = OpenAI(timeout=args.timeout, max_retries=0)

    usage = ExtractionUsage()
    recording = RecordingClient(client, api_key)
    handler = TextExtractionHandler(
        recording,
        model=model,
        reasoning_effort=effort,
        usage=usage,
        timeout_seconds=args.timeout,
    )
    prices = Prices(0.0, args.price_input_per_mtok, args.price_output_per_mtok)
    print(
        f"This run will make {plural(len(asked), 'request')}; a refused request stops it earlier.",
        flush=True,
    )

    records: list[tuple[MailMessage, ExtractionResult]] = []
    rows: list[dict[str, Any]] = []
    stopped: str | None = None
    for number, (message, (document, counts)) in enumerate(zip(asked, prepared, strict=True), 1):
        started = perf_counter()
        result = handler.extract(task, document)
        seconds = round(perf_counter() - started, 2)
        records.append((message, result))
        rows.append(json_row(message, result, counts, seconds))
        detail = (
            f"{len(result.kept)} kept, {len(result.dropped)} dropped"
            if not result.failed
            else result.message
        )
        print(
            f"[{number}/{len(asked)}] {message.source} | {result.outcome} | {detail} | {seconds} s"
        )
        if result.outcome in STOP_CODES:
            stopped = f"{result.outcome} at message {number}"
            print(f"Stopped after {message.source}: {result.outcome}: {result.message}")
            if result.outcome == "REQUEST_REFUSED":
                print(
                    "The API did not accept the request shape. Run with --dry-run to see "
                    "exactly what is sent."
                )
            else:
                print("Check the key and its access to the model.")
            break

    directory = args.out_dir or DEFAULT_OUT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True, exist_ok=True)
    summary = summarize(
        rows,
        read=len(messages),
        skipped=skipped,
        task=task,
        model=model,
        effort=effort,
        args=args,
        prices=prices,
        stopped=stopped,
        source=source,
    )
    (directory / "rows.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_workbook(directory / "register.xlsx", task, records)
    print_summary(summary)
    print(f"Report: {(directory / 'summary.json').resolve()}")
    print("rows.json and register.xlsx hold quotes from the mails; do not commit them.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
