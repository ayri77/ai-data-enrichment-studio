"""Who used a hosted demo and for what: counted events, never content.

An event is a kind, the label of the visitor's access code and a few numbers
or short fixed words (a model id, a capability id, a status). It never holds a
file name, a cell value, an instruction, a place name or an address of the
visitor; ``record`` refuses anything that is not such a small value, so a
careless call fails in a test instead of leaking text.

Events live in memory: a restart clears the summary. Where ``emit`` is set
(the hosted entry point) each event is also written as one JSON line to
standard output, which is where a hosting platform keeps its logs.
"""

import json
import logging
import sys
from collections import deque
from contextvars import ContextVar
from datetime import UTC, datetime
from threading import Lock
from typing import Any

# The label of the access code a request came with. "local" where no code is
# required. Set by the access middleware; tasks started by a request inherit it.
VISITOR: ContextVar[str] = ContextVar("visitor", default="local")

RECENT_EVENTS = 300
MAX_WORD = 64
_LINES = logging.getLogger("enrichment.usage")


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
        "plans": 0,
        "plans_failed": 0,
        "runs": 0,
        "rows": 0,
        "rows_filled": 0,
        "downloads": 0,
        "model_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "source_requests": {},
    }


class _StandardOutput(logging.Handler):
    """Write each line at once to whatever standard output is at that moment."""

    def emit(self, record: logging.LogRecord) -> None:
        print(record.getMessage(), file=sys.stdout, flush=True)


class UsageLog:
    """Events of this process and what they add up to per visitor."""

    def __init__(self, *, emit: bool = False) -> None:
        self.started_at = _now()
        self._lock = Lock()
        self._recent: deque[dict[str, Any]] = deque(maxlen=RECENT_EVENTS)
        self._kinds: dict[str, int] = {}
        self._visitors: dict[str, dict[str, Any]] = {}
        self._emit = emit
        if emit and not _LINES.handlers:
            _LINES.addHandler(_StandardOutput())
            _LINES.setLevel(logging.INFO)
            _LINES.propagate = False

    def record(self, kind: str, **details: Any) -> None:
        """Count one event for the visitor of the current request."""
        if not _small(kind) or not isinstance(kind, str):
            raise TypeError("An event kind is one short word.")
        for name, value in details.items():
            if not _allowed(value):
                raise TypeError(f"Usage detail {name!r} is not a number or a short word.")
        event = {"at": _now(), "who": VISITOR.get(), "kind": kind, **details}
        with self._lock:
            self._recent.append(event)
            self._kinds[kind] = self._kinds.get(kind, 0) + 1
            self._count(self._visitors.setdefault(event["who"], _blank()), event)
        if self._emit:
            _LINES.info("usage %s", json.dumps(event, sort_keys=True))

    @staticmethod
    def _count(visitor: dict[str, Any], event: dict[str, Any]) -> None:
        visitor["first_seen"] = visitor["first_seen"] or event["at"]
        visitor["last_seen"] = event["at"]
        kind = event["kind"]
        simple = {
            "visit": "visits",
            "upload": "uploads",
            "example": "examples",
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

    def summary(self) -> dict[str, Any]:
        """Totals since the process started, per visitor, and the latest events."""
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
                "now": _now(),
                "events": dict(sorted(self._kinds.items())),
                "visitors": sorted(visitors, key=lambda v: v["last_seen"] or "", reverse=True),
                # Newest first.
                "recent": list(reversed(self._recent)),
            }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
