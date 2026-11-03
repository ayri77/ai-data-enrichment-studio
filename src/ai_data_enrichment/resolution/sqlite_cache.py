"""Separate SQLite cache for *paired* successful operation and resolution report.

The legacy SQLiteOperationCache schema stores only OperationResult and must not
be reused for audited results: doing so loses alternatives and decision provenance.
Use a distinct database file, with strict schema, TTL and pair validation.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from json import dumps, loads
from math import isfinite
from pathlib import Path
from time import time
from typing import Literal, Self

from pydantic import ValidationError

from ai_data_enrichment.execution.models import EntityRef, GeoPoint, OperationResult
from ai_data_enrichment.execution.result_codec import deserialize_success, serialize_success
from ai_data_enrichment.resolution.models import ResolutionReport

_SCHEMA_VERSION = 1
_FORMAT = "audited-resolution:v1"


@dataclass(frozen=True)
class ResolutionCacheHit:
    """A cache hit is only valid when both result and decision are available."""

    operation: OperationResult
    report: ResolutionReport


@dataclass(frozen=True)
class ResolutionCacheLookup:
    status: Literal["hit", "miss", "expired", "invalid"]
    value: ResolutionCacheHit | None = None


def validate_paired_result(
    operation: OperationResult,
    report: ResolutionReport,
    *,
    provider_id: str,
) -> None:
    """Validate that a cacheable result really is supported by its audit."""
    if operation.status != "success" or report.decision.selected is None:
        raise ValueError("Only an audited, resolved operation can be cached.")
    if report.decision.status not in {"resolved", "auto_resolved"}:
        raise ValueError("Cacheable decision must have a verified selected candidate.")
    if report.candidate_set.provider_id != provider_id:
        raise ValueError("Audit provider identity differs from selected provider.")
    candidate = next(
        (c for c in report.candidate_set.candidates if c.reference == report.decision.selected),
        None,
    )
    if candidate is None or candidate.verification != "verified":
        raise ValueError("Audited operation must select a verified source candidate.")
    if not operation.evidence or operation.evidence != candidate.evidence:
        raise ValueError("Operation Evidence must equal selected candidate Evidence.")
    if isinstance(operation.value, EntityRef):
        if operation.value != report.decision.selected:
            raise ValueError("Selected source identity disagrees with operation EntityRef.")
    elif isinstance(operation.value, GeoPoint):
        facts = {attribute.name: attribute.value for attribute in candidate.attributes}
        if (
            type(facts.get("latitude")) is not float
            or type(facts.get("longitude")) is not float
            or operation.value.latitude != facts["latitude"]
            or operation.value.longitude != facts["longitude"]
        ):
            raise ValueError("Selected coordinates disagree with verified candidate.")
    else:
        raise ValueError("Unsupported audited resolution output type.")


def _digest(key: str) -> None:
    if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
        raise ValueError("Expected a lowercase SHA-256 operation digest.")


def _serialize(value: ResolutionCacheHit, provider_id: str) -> str:
    validate_paired_result(value.operation, value.report, provider_id=provider_id)
    return dumps(
        {
            "format": _FORMAT,
            "provider_id": provider_id,
            "operation": serialize_success(value.operation),
            "report": value.report.model_dump_json(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _deserialize(raw: str, provider_id: str) -> ResolutionCacheHit:
    try:
        data: object = loads(raw)
        if not isinstance(data, dict) or set(data) != {
            "format",
            "provider_id",
            "operation",
            "report",
        }:
            raise ValueError("Invalid audited cache entry shape.")
        if data["format"] != _FORMAT or data["provider_id"] != provider_id:
            raise ValueError("Audited cache provider/version mismatch.")
        if not isinstance(data["operation"], str) or not isinstance(data["report"], str):
            raise ValueError("Audited cache content must be JSON text.")
        operation = deserialize_success(data["operation"])
        report = ResolutionReport.model_validate_json(data["report"])
        validate_paired_result(operation, report, provider_id=provider_id)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError("Invalid paired resolution cache entry.") from exc
    return ResolutionCacheHit(operation=operation, report=report)


class SQLiteResolutionCache:
    """Store whole verified result/report pairs, never unresolved or failures."""

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
        self._connection.close()

    def _now(self) -> float:
        value = float(self._clock())
        if not isfinite(value):
            raise ValueError("Invalid SQLite resolution clock.")
        return value

    def _ensure_schema(self) -> None:
        db = self._connection
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if version == 0 and not tables:
            with db:
                db.execute(
                    """
                    CREATE TABLE resolution_cache (
                        key_digest TEXT PRIMARY KEY NOT NULL,
                        schema_version INTEGER NOT NULL,
                        provider_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        expires_at REAL NOT NULL
                    )
                    """
                )
                db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return
        if version != _SCHEMA_VERSION or tables != {"resolution_cache"}:
            raise ValueError("Incompatible resolution SQLite database; use a separate file.")
        cols = db.execute("PRAGMA table_info(resolution_cache)").fetchall()
        if [(row[1], row[2].upper(), row[3], row[5]) for row in cols] != [
            ("key_digest", "TEXT", 1, 1),
            ("schema_version", "INTEGER", 1, 0),
            ("provider_id", "TEXT", 1, 0),
            ("payload_json", "TEXT", 1, 0),
            ("expires_at", "REAL", 1, 0),
        ]:
            raise ValueError("Resolution SQLite table has an incompatible layout.")

    def get(self, key_digest: str, *, provider_id: str) -> ResolutionCacheLookup:
        _digest(key_digest)
        row = self._connection.execute(
            """
            SELECT schema_version, provider_id, payload_json, expires_at
            FROM resolution_cache WHERE key_digest=?
            """,
            (key_digest,),
        ).fetchone()
        if row is None:
            return ResolutionCacheLookup(status="miss")
        version, stored_provider, raw, expiry = row
        if type(expiry) not in (int, float) or not isfinite(expiry) or self._now() >= expiry:
            self._delete(key_digest)
            return ResolutionCacheLookup(status="expired")
        try:
            if version != _SCHEMA_VERSION or stored_provider != provider_id:
                raise ValueError("Incompatible cached provider.")
            if not isinstance(raw, str):
                raise ValueError("Invalid cached JSON.")
            result = _deserialize(raw, provider_id)
        except ValueError:
            self._delete(key_digest)
            return ResolutionCacheLookup(status="invalid")
        return ResolutionCacheLookup(status="hit", value=result)

    def put(
        self,
        key_digest: str,
        value: ResolutionCacheHit,
        *,
        provider_id: str,
        ttl_seconds: float,
    ) -> None:
        _digest(key_digest)
        if not provider_id.strip():
            raise ValueError("Provider identity must not be blank.")
        if type(ttl_seconds) not in (int, float) or not isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("Resolution cache TTL must be positive and finite.")
        payload = _serialize(value, provider_id)
        expiry = self._now() + ttl_seconds
        if not isfinite(expiry):
            raise ValueError("Resolution cache expiry must be finite.")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO resolution_cache
                    (key_digest, schema_version, provider_id, payload_json, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key_digest) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    provider_id=excluded.provider_id,
                    payload_json=excluded.payload_json,
                    expires_at=excluded.expires_at
                """,
                (key_digest, _SCHEMA_VERSION, provider_id, payload, expiry),
            )

    def _delete(self, key_digest: str) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM resolution_cache WHERE key_digest=?", (key_digest,)
            )
