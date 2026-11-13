"""Bounds of one run.

The defaults are sized for the public demo: a run stays short, cheap and polite
to the free external sources. A local experiment may raise them through the
environment to measure how the pipeline behaves on larger tables; the
architecture is not designed for that scale (one sequential worker, no partial
result, no checkpoint), which is exactly what such an experiment is for.

Values are read once at import. The run worker is a separate process that
imports this module again and inherits the same environment.
"""

import os


def bounded(name: str, default: int, ceiling: int) -> int:
    """A whole number from the environment, or the default when it is unset."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be a whole number.") from None
    if not 1 <= value <= ceiling:
        raise RuntimeError(f"{name} must be between 1 and {ceiling}.")
    return value


# What a table may hold to be read and looked through: data rows and cells of
# the selection. Fixed; a run takes its rows from such a table.
MAX_TABLE_ROWS = 5000
MAX_TABLE_CELLS = 100_000
# Data rows one run fills.
MAX_RUN_ROWS = bounded("ENRICHMENT_MAX_RUN_ROWS", 20, MAX_TABLE_ROWS)
# HTTP attempts to one external source during one run.
MAX_PROVIDER_REQUESTS = bounded("ENRICHMENT_MAX_PROVIDER_REQUESTS", 400, 1_000_000)
# Seconds one run may spend waiting on pacing and retries, per external source.
MAX_PROVIDER_WAIT_SECONDS = bounded("ENRICHMENT_MAX_PROVIDER_WAIT_SECONDS", 15, 86_400)
# Upper bound for the run timeout a client may ask for.
MAX_TIMEOUT_SECONDS = bounded("ENRICHMENT_MAX_TIMEOUT_SECONDS", 300, 86_400)
DEFAULT_TIMEOUT_SECONDS = min(180, MAX_TIMEOUT_SECONDS)
# How long a value one run of a plan confirmed may be reused by a later run of
# the same plan (a resume after review, a retry) instead of asking the source
# again. Fixed: long enough for a review, short enough that a source's data is
# not treated as permanent.
RESULT_REUSE_SECONDS = 3600
