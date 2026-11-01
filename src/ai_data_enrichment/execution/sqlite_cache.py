"""Versioned, local SQLite storage for successful typed operation results.

The adapter has no access to handlers, DAG plans, or provider credentials.
Callers supply an explicitly approved operation digest and a positive TTL.
"""

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import sqlite3
from time import time
from typing import Literal, Self

from ai_data_enrichment.execution.models import OperationResult
from ai_data_enrichment.execution.result_codec import deserialize_success, serialize_success

SQLITE_SCHEMA_VERSION = 1
_TABLE = "operation_cache"
_EXPECTED_COLUMNS = (
    ("key_digest", "TEXT", 1),
    ("schema_version", "INTEGER", 0),
    ("result_json", "TEXT", 0),
    ("created_at_epoch", "REAL", 0),
    ("expires_at_epoch", "REAL", 0),
    ("capability_id", "TEXT", 0),
    ("provider_id", "TEXT", 0),
)


class CacheSchemaError(RuntimeError):
    """An existing database cannot safely be read by this cache version."""


@dataclass(frozen=True)
class CacheLookup:
    """Describe a typed hit or an explicit reason for a cache miss."""

    status: Literal["hit", "miss", "expired", "invalid"]
    result: OperationResult | None = None


def _valid_digest(key_digest: str) -> None:
    if (
        not isinstance(key_digest, str)
        or len(key_digest) != 64
        or any(char not in "0123456789abcdef" for char in key_digest)
    ):
        raise ValueError("Cache key must be a lowercase 64-character SHA-256 digest.")


class SQLiteOperationCache:
    """A locally owned SQLite cache; close it explicitly or use a with-block."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], float] = time,
    ) -> None:
        self._clock = clock
        self._connection = sqlite3.connect(str(path))
        try:
            self._ensure_schema()
        except Exception:
            self._connection.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying database connection."""
        self._connection.close()

    def _now(self) -> float:
        now = float(self._clock())
        if not isfinite(now):
            raise ValueError("Cache clock must return a finite Unix timestamp.")
        return now

    def _ensure_schema(self) -> None:
        connection = self._connection
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SQLITE_SCHEMA_VERSION):
            raise CacheSchemaError(
                f"Unsupported SQLite cache schema version {version}; "
                "use a separate cache file or a supported migration."
            )

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        if version == 0:
            if tables:
                raise CacheSchemaError(
                    "Refusing to initialize an existing unversioned database."
                )
            with connection:
                connection.execute(
                    """
                    CREATE TABLE operation_cache (
                        key_digest TEXT PRIMARY KEY NOT NULL,
                        schema_version INTEGER NOT NULL,
                        result_json TEXT NOT NULL,
                        created_at_epoch REAL NOT NULL,
                        expires_at_epoch REAL NOT NULL,
                        capability_id TEXT NOT NULL,
                        provider_id TEXT NOT NULL
                    )
                    """
                )
                connection.execute(f"PRAGMA user_version = {SQLITE_SCHEMA_VERSION}")
            return

        if tables != {_TABLE}:
            raise CacheSchemaError("SQLite cache schema contains unexpected or missing tables.")
        columns = connection.execute(f"PRAGMA table_info({_TABLE})").fetchall()
        actual = [(row[1], row[2].upper(), row[5]) for row in columns]
        if actual != list(_EXPECTED_COLUMNS):
            raise CacheSchemaError("SQLite cache table has an incompatible layout.")
        if any(row[3] != 1 for row in columns):
            raise CacheSchemaError("SQLite cache columns have incompatible nullability.")

    def get(self, key_digest: str) -> CacheLookup:
        """Read a still-valid, strictly validated success or describe a miss.

        A malformed *entry* is deleted; a schema or SQLite I/O error propagates.
        """
        _valid_digest(key_digest)
        row = self._connection.execute(
            """
            SELECT schema_version, result_json, expires_at_epoch
            FROM operation_cache WHERE key_digest = ?
            """,
            (key_digest,),
        ).fetchone()
        if row is None:
            return CacheLookup(status="miss")

        entry_version, payload, expires_at = row
        now = self._now()
        if (
            type(expires_at) not in (float, int)
            or not isfinite(expires_at)
            or now >= expires_at
        ):
            with self._connection:
                self._connection.execute(
                    "DELETE FROM operation_cache WHERE key_digest = ?", (key_digest,)
                )
            return CacheLookup(status="expired")

        try:
            if type(entry_version) is not int or entry_version != SQLITE_SCHEMA_VERSION:
                raise ValueError("Incompatible cache entry version.")
            if not isinstance(payload, str):
                raise ValueError("Cache entry result must be JSON text.")
            result = deserialize_success(payload)
        except ValueError:
            with self._connection:
                self._connection.execute(
                    "DELETE FROM operation_cache WHERE key_digest = ?", (key_digest,)
                )
            return CacheLookup(status="invalid")

        return CacheLookup(status="hit", result=result)

    def put(
        self,
        key_digest: str,
        result: OperationResult,
        *,
        ttl_seconds: float,
        capability_id: str,
        provider_id: str,
    ) -> None:
        """Store an approved success with an explicit time to live.

        Caller enforces provider eligibility and chooses a source-appropriate TTL.
        Failures cannot be stored. Existing keys are replaced atomically.
        """
        _valid_digest(key_digest)
        if type(ttl_seconds) not in (int, float) or not isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("Cache TTL must be positive and finite.")
        if not capability_id.strip() or not provider_id.strip():
            raise ValueError("Capability and provider IDs must not be blank.")

        payload = serialize_success(result)
        created = self._now()
        expires = created + ttl_seconds
        if not isfinite(expires):
            raise ValueError("Cache expiration must be a finite timestamp.")

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO operation_cache (
                    key_digest, schema_version, result_json, created_at_epoch,
                    expires_at_epoch, capability_id, provider_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key_digest) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    result_json = excluded.result_json,
                    created_at_epoch = excluded.created_at_epoch,
                    expires_at_epoch = excluded.expires_at_epoch,
                    capability_id = excluded.capability_id,
                    provider_id = excluded.provider_id
                """,
                (
                    key_digest,
                    SQLITE_SCHEMA_VERSION,
                    payload,
                    created,
                    expires,
                    capability_id,
                    provider_id,
                ),
            )
