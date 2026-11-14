"""Entry point for a publicly reachable demo: one process serves API and frontend.

Start with:

    uvicorn --factory ai_data_enrichment.hosted:create_hosted_app --host 0.0.0.0 --port 10000

Everything is configured from the environment, never from the browser:

- `ENRICHMENT_ACCESS_CODES`: one code per visitor, as `label:code` entries
  separated by commas, semicolons or line breaks, for example
  `guest01:Xk3...,guest02:p9Q...`. The label is what the usage summary shows;
  the code is what the visitor enters. `python -m scripts.make_access_codes`
  prints such entries.
- `ENRICHMENT_ACCESS_CODE`: one more code under the label "shared", as before.
  At least one code must be set by either variable. Without any the app
  refuses to start, so a deployment can never be open by accident.
- `ENRICHMENT_ADMIN_CODE` (optional, at least 12 characters): opens the usage
  summary and nothing else. Without it there is no such summary.
- `OPENAI_API_KEY`, `OPENAI_MODEL`: planning provider (model optional).
- `ENRICHMENT_ALLOW_NETWORK=1`: server network gate for live requests.
- `ENRICHMENT_STATIC_DIR` (default `frontend/dist`): the built frontend.

State is in memory and in a temporary directory, as in the local launcher: run
exactly one process, and expect a restart to clear uploads, plans, jobs and the
usage summary. Counted events are also written to standard output, one JSON
line each, where the hosting platform keeps its logs.
"""

import os
import re
from pathlib import Path

from fastapi import FastAPI

from ai_data_enrichment.api import SHARED_VISITOR, create_app

MIN_ACCESS_CODE_LENGTH = 8
MIN_ADMIN_CODE_LENGTH = 12
MAX_VISITORS = 200
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")


def _usable(code: str, minimum: int) -> bool:
    return (
        len(code) >= minimum
        and code.isascii()
        and code.isprintable()
        and not any(c.isspace() or c in ",;" for c in code)
    )


def visitor_codes(text: str) -> dict[str, str]:
    """Read `label:code` entries; an error names the entry by its position only."""
    codes: dict[str, str] = {}
    entries = [entry for entry in re.split(r"[,;\s]+", text) if entry]
    if len(entries) > MAX_VISITORS:
        raise RuntimeError(f"ENRICHMENT_ACCESS_CODES holds more than {MAX_VISITORS} entries.")
    for position, entry in enumerate(entries, 1):
        label, separator, code = entry.partition(":")
        problem = (
            "is not label:code"
            if not separator
            else "has a label other than letters, digits, - and _ (up to 40)"
            if not _LABEL.fullmatch(label)
            else f"uses the reserved label {SHARED_VISITOR!r}"
            if label == SHARED_VISITOR
            else "repeats a label"
            if label in codes
            else f"has a code shorter than {MIN_ACCESS_CODE_LENGTH} ASCII characters"
            if not _usable(code, MIN_ACCESS_CODE_LENGTH)
            else "repeats a code"
            if code in codes.values()
            else None
        )
        if problem:
            # The entry itself is not echoed: it holds a code.
            raise RuntimeError(f"ENRICHMENT_ACCESS_CODES: entry {position} {problem}.")
        codes[label] = code
    return codes


def create_hosted_app() -> FastAPI:
    codes = visitor_codes(os.getenv("ENRICHMENT_ACCESS_CODES", ""))
    shared = os.getenv("ENRICHMENT_ACCESS_CODE", "")
    if shared and not _usable(shared, MIN_ACCESS_CODE_LENGTH):
        raise RuntimeError(
            "Set ENRICHMENT_ACCESS_CODE to at least 8 ASCII characters without spaces."
        )
    if shared and shared in codes.values():
        raise RuntimeError("ENRICHMENT_ACCESS_CODE repeats a code of ENRICHMENT_ACCESS_CODES.")
    if not shared and not codes:
        raise RuntimeError(
            "Set ENRICHMENT_ACCESS_CODES (label:code entries) or ENRICHMENT_ACCESS_CODE "
            "(at least 8 ASCII characters without spaces)."
        )
    admin = os.getenv("ENRICHMENT_ADMIN_CODE", "")
    if admin and not _usable(admin, MIN_ADMIN_CODE_LENGTH):
        raise RuntimeError(
            "Set ENRICHMENT_ADMIN_CODE to at least 12 ASCII characters without spaces."
        )
    if admin and (admin == shared or admin in codes.values()):
        raise RuntimeError("ENRICHMENT_ADMIN_CODE must differ from every access code.")
    static_dir = Path(os.getenv("ENRICHMENT_STATIC_DIR", "frontend/dist"))
    if not (static_dir / "index.html").is_file():
        raise RuntimeError(f"Built frontend not found in {static_dir}. Run `npm run build` first.")
    # Browser configuration of the provider stays disabled: this is not the local launcher.
    return create_app(
        access_code=shared or None,
        access_codes=codes,
        admin_code=admin or None,
        usage_lines=True,
        static_dir=static_dir,
    )
