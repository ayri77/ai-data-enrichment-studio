"""Who used a hosted demo and for what: counted events, never content.

An event is a kind, the label of the visitor's access code and a few numbers
or short fixed words (a model id, a capability id, a status). It never holds a
file name, a cell value, an instruction, a place name or an address of the
visitor; ``record`` refuses anything that is not such a small value, so a
careless call fails in a test instead of leaking text.

Events are counted in memory. Behind the log sits a ``UsageStore``: with none, a
restart clears the summary; with a ``FileUsageStore`` every event is also appended to
a file and replayed, through the same counting code, when the log is made again.
A store for a database, for a host whose disk does not survive a restart, is one more
class with the same five methods; nothing else changes. Where ``emit`` is set (the
hosted entry point) each event is also written as one JSON line to standard output,
which is where a hosting platform keeps its logs.
"""

import contextlib
import copy
import json
import logging
import os
import sys
from collections import deque
from collections.abc import Iterable, Iterator
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

# The label of the access code a request came with. "local" where no code is
# required. Set by the access middleware; tasks started by a request inherit it.
VISITOR: ContextVar[str] = ContextVar("visitor", default="local")

RECENT_EVENTS = 300
MAX_WORD = 64
# A usage file larger than this is rewritten once: the totals of what it held, then
# the latest RECENT_EVENTS events.
MAX_FILE_BYTES = 5 * 1024 * 1024
# The kind of the first record of a compacted file. No event may have it.
TOTALS = "totals"
# Reason words of a log that is not kept in a file.
OFF = "off"
WRITE_FAILED = "write_failed"
_LINES = logging.getLogger("enrichment.usage")
_WARNINGS = logging.getLogger(__name__)


def _small(value: Any) -> bool:
    """A number, a flag, nothing, or one short word without spaces."""
    if value is None or isinstance(value, (bool, int, float)):
        return True
    return (
        isinstance(value, str)
        and len(value) <= MAX_WORD
        and value.isascii()
        and not any(c.isspace() for c in value)
    )


def _allowed(value: Any) -> bool:
    if _small(value):
        return True
    if isinstance(value, dict):
        return all(_small(k) and isinstance(k, str) and _small(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return all(_small(item) for item in value)
    return False


def _blank() -> dict[str, Any]:
    return {
        "first_seen": None,
        "last_seen": None,
        "visits": 0,
        "uploads": 0,
        "examples": 0,
        "mail_imports": 0,
        "plans": 0,
        "plans_failed": 0,
        "runs": 0,
        "rows": 0,
        "rows_filled": 0,
        "downloads": 0,
        "model_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        # Searches the web lookup's model made, as the provider reported them.
        "web_searches": 0,
        "source_requests": {},
    }


class _StandardOutput(logging.Handler):
    """Write each line at once to whatever standard output is at that moment."""

    def emit(self, record: logging.LogRecord) -> None:
        print(record.getMessage(), file=sys.stdout, flush=True)


class UsageStore(Protocol):
    """Where events are kept beyond the process. Called under the log's lock."""

    def append(self, event: dict[str, Any]) -> None:
        """Keep one event. Raises OSError when it cannot."""
        ...

    def read(self) -> Iterator[Any]:
        """What each kept record decodes to, oldest first; None for one that cannot be decoded."""
        ...

    def oversize(self) -> bool:
        """True when the store wants ``rewrite`` before it takes more."""
        ...

    def rewrite(self, records: Iterable[dict[str, Any]]) -> None:
        """Replace everything kept by these records. Raises OSError when it cannot."""
        ...

    def close(self) -> None:
        """Let go of what is held open; a later ``append`` opens it again."""
        ...


class FileUsageStore:
    """One JSON object per line, appended and flushed per event."""

    def __init__(self, path: Path, *, limit: int = MAX_FILE_BYTES) -> None:
        self.path = path
        self.limit = limit
        # Raised after a rewrite that is itself larger than the limit, so that a small
        # limit cannot make every event rewrite the file.
        self._threshold = limit
        self._file: Any = None
        self._size = 0

    def read(self) -> Iterator[Any]:
        try:
            handle = self.path.open("r", encoding="utf-8", errors="replace", newline="\n")
        except FileNotFoundError:
            return
        with handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    yield None

    def _open(self) -> Any:
        if self._file is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            handle = os.fdopen(descriptor, "ab")
            try:
                self._size = handle.seek(0, os.SEEK_END)
                if self._size:
                    # A write cut off by a crash leaves a line without its end; the next
                    # event must not be glued to it.
                    with self.path.open("rb") as existing:
                        existing.seek(-1, os.SEEK_END)
                        if existing.read(1) != b"\n":
                            self._size += handle.write(b"\n")
            except BaseException:
                handle.close()
                raise
            self._file = handle
        return self._file

    def append(self, event: dict[str, Any]) -> None:
        handle = self._open()
        line = (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
        handle.write(line)
        handle.flush()
        self._size += len(line)

    def oversize(self) -> bool:
        return self._size > self._threshold

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def rewrite(self, records: Iterable[dict[str, Any]]) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        beside = self.path.with_name(self.path.name + ".new")
        try:
            descriptor = os.open(beside, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                for record in records:
                    handle.write((json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(beside, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                beside.unlink()
            raise
        self._size = self.path.stat().st_size
        self._threshold = max(self.limit, 2 * self._size)


def default_usage_file() -> Path:
    """Where the local launcher keeps the usage file: in the user's state folder.

    ``$XDG_STATE_HOME`` where set, else ``%LOCALAPPDATA%`` on Windows, else
    ``~/.local/state``; below it ``ai-data-enrichment-studio/usage/events.jsonl``.
    Never inside the working directory, so it cannot end up in a repository.
    """
    state = os.getenv("XDG_STATE_HOME") or (
        os.getenv("LOCALAPPDATA") if sys.platform == "win32" else None
    )
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "ai-data-enrichment-studio" / "usage" / "events.jsonl"


def parse_usage_file(value: str | None) -> str | None:
    """The setting ``ENRICHMENT_USAGE_FILE``: None when unset, "off", or a path."""
    value = (value or "").strip()
    return OFF if value.lower() == OFF else value or None


def store_for(setting: str | None) -> UsageStore | None:
    """The store a setting names: none for unset or "off", else a file."""
    if setting is None or setting == OFF:
        return None
    return FileUsageStore(Path(setting).expanduser())


def summary_open_without_code(*, local: bool, access_codes_set: bool, admin_code_set: bool) -> bool:
    """Whether ``GET /api/usage`` answers without an admin code.

    Only when the application was started by the local launcher (which listens on
    the loopback address alone) AND neither access codes nor an admin code are set:
    then the only person who can reach it is the owner of the machine. An admin code,
    if there is one, is required as ever, locally too. A hosted application never
    answers without it, and without one it does not answer at all.
    """
    return local and not access_codes_set and not admin_code_set


class UsageLog:
    """Events of this process, and of earlier ones where there is a store, and their sums."""

    def __init__(self, *, emit: bool = False, store: UsageStore | None = None) -> None:
        self.started_at = _now()
        self._lock = Lock()
        self._recent: deque[dict[str, Any]] = deque(maxlen=RECENT_EVENTS)
        self._kinds: dict[str, int] = {}
        self._visitors: dict[str, dict[str, Any]] = {}
        # What the events that have left the recent list added up to: it is what a
        # compacted file starts with.
        self._older_kinds: dict[str, int] = {}
        self._older_visitors: dict[str, dict[str, Any]] = {}
        self._first_at: str | None = None
        self._store = store
        self._failure: str | None = OFF if store is None else None
        self.skipped_lines = 0
        self._emit = emit
        if emit and not _LINES.handlers:
            _LINES.addHandler(_StandardOutput())
            _LINES.setLevel(logging.INFO)
            _LINES.propagate = False
        if store is not None:
            self._replay(store)

    def _replay(self, store: UsageStore) -> None:
        try:
            for position, record in enumerate(store.read()):
                if position == 0 and isinstance(record, dict) and record.get("kind") == TOTALS:
                    accepted = self._load_totals(record)
                else:
                    accepted = self._valid(record) and self._apply(record)
                if not accepted:
                    self.skipped_lines += 1
        except OSError:
            self._degrade()

    @staticmethod
    def _valid(event: Any) -> bool:
        """An event as ``record`` makes it: a small kind, who and time, small details."""
        return (
            isinstance(event, dict)
            and all(isinstance(event.get(key), str) for key in ("at", "who", "kind"))
            and event["kind"] != TOTALS
            and all(_small(key) and isinstance(key, str) for key in event)
            and all(_allowed(value) for value in event.values())
        )

    def _load_totals(self, record: dict[str, Any]) -> bool:
        since, kinds, visitors = record.get("since"), record.get("events"), record.get("visitors")
        if not (_small(since) and (since is None or isinstance(since, str))):
            return False
        if not isinstance(kinds, dict) or not isinstance(visitors, dict):
            return False
        if not all(_small(k) and _count_ok(v) for k, v in kinds.items()):
            return False
        loaded: dict[str, dict[str, Any]] = {}
        for who, data in visitors.items():
            if not (_small(who) and isinstance(data, dict)) or not set(data) <= set(_blank()):
                return False
            visitor = {**_blank(), **data}
            counters = {k: v for k, v in visitor.items() if k not in _NOT_COUNTERS}
            sources = visitor["source_requests"]
            if not (
                all(_count_ok(v) for v in counters.values())
                and all(
                    _small(v) and (v is None or isinstance(v, str))
                    for v in (visitor["first_seen"], visitor["last_seen"])
                )
                and isinstance(sources, dict)
                and all(_small(k) and _count_ok(v) for k, v in sources.items())
            ):
                return False
            loaded[who] = visitor
        self._first_at = since
        self._kinds, self._older_kinds = dict(kinds), dict(kinds)
        self._visitors, self._older_visitors = loaded, copy.deepcopy(loaded)
        return True

    def _apply(self, event: dict[str, Any]) -> bool:
        """Count one event; False, with nothing counted, when its numbers do not add up."""
        try:
            # On a scratch visitor first, so that a bad number cannot leave half a count.
            self._count(_blank(), event)
        except (ValueError, TypeError, OverflowError, AttributeError):
            return False
        self._first_at = self._first_at or event["at"]
        if len(self._recent) == self._recent.maxlen:
            gone = self._recent[0]
            self._kinds_add(self._older_kinds, gone)
            self._count(self._older_visitors.setdefault(gone["who"], _blank()), gone)
        self._recent.append(event)
        self._kinds_add(self._kinds, event)
        self._count(self._visitors.setdefault(event["who"], _blank()), event)
        return True

    @staticmethod
    def _kinds_add(kinds: dict[str, int], event: dict[str, Any]) -> None:
        kinds[event["kind"]] = kinds.get(event["kind"], 0) + 1

    def record(self, kind: str, **details: Any) -> None:
        """Count one event for the visitor of the current request."""
        if not _small(kind) or not isinstance(kind, str) or kind == TOTALS:
            raise TypeError("An event kind is one short word.")
        for name, value in details.items():
            if not _allowed(value):
                raise TypeError(f"Usage detail {name!r} is not a number or a short word.")
        event = {"at": _now(), "who": VISITOR.get(), "kind": kind, **details}
        with self._lock:
            if not self._apply(event):
                raise TypeError("A usage detail that is counted must be a number.")
            self._keep(event)
        if self._emit:
            _LINES.info("usage %s", json.dumps(event, sort_keys=True))

    def close(self) -> None:
        with self._lock:
            if self._store is not None:
                with contextlib.suppress(OSError):
                    self._store.close()

    def _keep(self, event: dict[str, Any]) -> None:
        if self._store is None or self._failure:
            return
        try:
            self._store.append(event)
            if self._store.oversize():
                self._store.rewrite(self._compacted())
        except OSError:
            self._degrade()

    def _degrade(self) -> None:
        """Counting goes on in memory. The warning says nothing of any event."""
        if self._failure is None:
            _WARNINGS.warning("The usage file cannot be written; counting in memory only.")
        self._failure = WRITE_FAILED

    def _compacted(self) -> Iterator[dict[str, Any]]:
        yield {
            "kind": TOTALS,
            "since": self._first_at,
            "events": dict(sorted(self._older_kinds.items())),
            "visitors": copy.deepcopy(self._older_visitors),
        }
        yield from self._recent

    @staticmethod
    def _count(visitor: dict[str, Any], event: dict[str, Any]) -> None:
        visitor["first_seen"] = visitor["first_seen"] or event["at"]
        visitor["last_seen"] = event["at"]
        kind = event["kind"]
        simple = {
            "visit": "visits",
            "upload": "uploads",
            "example": "examples",
            "mail_import": "mail_imports",
            "plan": "plans",
            "plan_failed": "plans_failed",
            "run_started": "runs",
            "download": "downloads",
        }
        if kind in simple:
            visitor[simple[kind]] += 1
        elif kind == "run_finished":
            visitor["rows"] += int(event.get("rows") or 0)
            visitor["rows_filled"] += int(event.get("rows_filled") or 0)
            for source, requests in (event.get("source_requests") or {}).items():
                visitor["source_requests"][source] = visitor["source_requests"].get(
                    source, 0
                ) + int(requests or 0)
        elif kind == "model":
            visitor["model_requests"] += int(event.get("requests") or 0)
            visitor["input_tokens"] += int(event.get("input_tokens") or 0)
            visitor["output_tokens"] += int(event.get("output_tokens") or 0)
            visitor["web_searches"] += int(event.get("web_searches") or 0)

    def summary(self) -> dict[str, Any]:
        """Totals since the first counted event, per visitor, and the latest events."""
        with self._lock:
            visitors = [
                {
                    "who": who,
                    **{k: (dict(v) if isinstance(v, dict) else v) for k, v in data.items()},
                }
                for who, data in self._visitors.items()
            ]
            return {
                "started_at": self.started_at,
                "process_started_at": self.started_at,
                "since": self._first_at,
                "persistent": self._failure is None,
                **({"reason": self._failure} if self._failure else {}),
                "skipped_lines": self.skipped_lines,
                "now": _now(),
                "events": dict(sorted(self._kinds.items())),
                "visitors": sorted(visitors, key=lambda v: v["last_seen"] or "", reverse=True),
                # Newest first.
                "recent": list(reversed(self._recent)),
            }


_NOT_COUNTERS = frozenset({"first_seen", "last_seen", "source_requests"})


def _count_ok(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
