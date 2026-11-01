"""Offline SQLite cache tests using real temporary database files and a fake clock."""

import sqlite3
from pathlib import Path

import pytest

from ai_data_enrichment.execution.models import (
    EntityRef,
    Evidence,
    GeoPoint,
    OperationResult,
    Quantity,
    RuntimeValue,
)
from ai_data_enrichment.execution.sqlite_cache import (
    SQLITE_SCHEMA_VERSION,
    CacheSchemaError,
    SQLiteOperationCache,
)

KEY_A = "a" * 64
KEY_B = "b" * 64


class FakeClock:
    def __init__(self, instant: float = 1000.0) -> None:
        self.instant = instant

    def __call__(self) -> float:
        return self.instant


def success(value: RuntimeValue = "Everest") -> OperationResult:
    return OperationResult(
        status="success",
        value=value,
        evidence=(
            Evidence(source="wikidata", reference="Q513:P31"),
            Evidence(source="wikidata", reference="Q513:P2044"),
        ),
    )


def store(
    cache: SQLiteOperationCache,
    key: str,
    result: OperationResult | None = None,
    *,
    ttl: float = 50.0,
) -> None:
    cache.put(
        key,
        result if result is not None else success(),
        ttl_seconds=ttl,
        capability_id="resolve_entity",
        provider_id="wikidata",
    )


def test_new_database_is_versioned_and_unknown_key_is_a_miss(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite3"
    clock = FakeClock()
    with SQLiteOperationCache(path, clock=clock) as cache:
        assert cache.get(KEY_A).status == "miss"
        store(cache, KEY_A)
        assert cache.get(KEY_A).status == "hit"

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SQLITE_SCHEMA_VERSION
        row = connection.execute(
            "SELECT schema_version, capability_id, provider_id FROM operation_cache"
        ).fetchone()
        assert row == (SQLITE_SCHEMA_VERSION, "resolve_entity", "wikidata")


@pytest.mark.parametrize(
    "value",
    [
        "Говерла",
        8848.86,
        -0.0,
        GeoPoint(latitude=27.9, longitude=86.9),
        EntityRef(source="wikidata", entity_id="Q513"),
        Quantity(value=8848.86, unit="m"),
    ],
)
def test_success_and_evidence_survive_close_and_reopen(
    tmp_path: Path, value: RuntimeValue
) -> None:
    path = tmp_path / "persist.sqlite3"
    original = success(value)

    with SQLiteOperationCache(path, clock=FakeClock()) as cache:
        store(cache, KEY_A, original)

    with SQLiteOperationCache(path, clock=FakeClock()) as reopened:
        hit = reopened.get(KEY_A)
        assert hit.status == "hit"
        assert hit.result == original
        assert hit.result is not None
        assert type(hit.result.value) is type(value)
        assert hit.result.evidence == original.evidence


def test_ttl_boundary_expires_exactly_and_deletes_stale_entry(tmp_path: Path) -> None:
    clock = FakeClock(100.0)
    with SQLiteOperationCache(tmp_path / "ttl.db", clock=clock) as cache:
        store(cache, KEY_A, ttl=10.0)
        clock.instant = 109.999
        assert cache.get(KEY_A).status == "hit"
        clock.instant = 110.0
        assert cache.get(KEY_A).status == "expired"
        assert cache.get(KEY_A).status == "miss"
        store(cache, KEY_A, success("refreshed"), ttl=2.0)
        assert cache.get(KEY_A).result == success("refreshed")


def test_independent_keys_and_expiration_in_same_database(tmp_path: Path) -> None:
    clock = FakeClock()
    with SQLiteOperationCache(tmp_path / "cache.db", clock=clock) as cache:
        store(cache, KEY_A, success("A"), ttl=1.0)
        store(cache, KEY_B, success("B"), ttl=100.0)
        clock.instant += 2.0
        assert cache.get(KEY_A).status == "expired"
        assert cache.get(KEY_B).result == success("B")


def test_corrupt_payload_and_entry_version_only_invalidate_target_row(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.db"
    with SQLiteOperationCache(path, clock=FakeClock()) as cache:
        store(cache, KEY_A)
        store(cache, KEY_B, success("safe"))

        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE operation_cache SET result_json = ? WHERE key_digest = ?",
                ("{broken-json", KEY_A),
            )

        assert cache.get(KEY_A).status == "invalid"
        assert cache.get(KEY_A).status == "miss"
        assert cache.get(KEY_B).result == success("safe")

        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE operation_cache SET schema_version = 999 WHERE key_digest = ?",
                (KEY_B,),
            )
        assert cache.get(KEY_B).status == "invalid"
        assert cache.get(KEY_B).status == "miss"


def test_corrupt_typed_value_is_rejected_without_being_returned(tmp_path: Path) -> None:
    path = tmp_path / "bad-typed.db"
    with SQLiteOperationCache(path, clock=FakeClock()) as cache:
        store(cache, KEY_A)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE operation_cache SET result_json = ? WHERE key_digest = ?",
                (
                    '{"format":"operation-result:v1","status":"success",'
                    '"value":{"type":"quantity","value":"nan","unit":"m"},'
                    '"evidence":[]}',
                    KEY_A,
                ),
            )
        assert cache.get(KEY_A).status == "invalid"


def test_storage_rejects_failures_without_overwriting_existing_success(tmp_path: Path) -> None:
    with SQLiteOperationCache(tmp_path / "only-success.db") as cache:
        store(cache, KEY_A)
        failure = OperationResult(
            status="failure", error_code="NOT_FOUND", message="No mountain."
        )
        with pytest.raises(ValueError, match="Only successful"):
            store(cache, KEY_A, failure)
        assert cache.get(KEY_A).result == success()


@pytest.mark.parametrize("ttl", [0.0, -1.0, float("inf"), float("nan"), True])
def test_invalid_ttl_is_rejected_before_sqlite_write(tmp_path: Path, ttl: float) -> None:
    with SQLiteOperationCache(tmp_path / "invalid-ttl.db") as cache:
        with pytest.raises(ValueError, match="TTL"):
            store(cache, KEY_A, ttl=ttl)
        assert cache.get(KEY_A).status == "miss"


@pytest.mark.parametrize("digest", ["", "bad", "A" * 64, "g" * 64])
def test_digest_validation_precedes_storage(tmp_path: Path, digest: str) -> None:
    with SQLiteOperationCache(tmp_path / "digest.db") as cache:
        with pytest.raises(ValueError, match="digest"):
            cache.get(digest)
        with pytest.raises(ValueError, match="digest"):
            store(cache, digest)


def test_expiration_clock_nonfinite_or_overflow_is_rejected(tmp_path: Path) -> None:
    clock = FakeClock(float("nan"))
    with SQLiteOperationCache(tmp_path / "bad-clock.db", clock=clock) as cache:
        with pytest.raises(ValueError, match="finite Unix"):
            store(cache, KEY_A)
        assert cache.get(KEY_A).status == "miss"

    clock.instant = 1.0e308
    with SQLiteOperationCache(tmp_path / "big-clock.db", clock=clock) as cache:
        with pytest.raises(ValueError, match="expiration"):
            store(cache, KEY_A, ttl=1.0e308)


def test_existing_unversioned_db_is_never_replaced(tmp_path: Path) -> None:
    path = tmp_path / "unrelated.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sensitive_records (value TEXT)")
        connection.execute("INSERT INTO sensitive_records VALUES ('keep me')")

    with pytest.raises(CacheSchemaError, match="unversioned"):
        SQLiteOperationCache(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM sensitive_records").fetchone() == (
            "keep me",
        )


@pytest.mark.parametrize("version", [2, 999])
def test_unrecognized_schema_version_is_not_migrated(tmp_path: Path, version: int) -> None:
    path = tmp_path / "future.db"
    with SQLiteOperationCache(path):
        pass
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {version}")
    with pytest.raises(CacheSchemaError, match="Unsupported"):
        SQLiteOperationCache(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == version


def test_altered_schema_is_rejected_without_recreation(tmp_path: Path) -> None:
    path = tmp_path / "modified.db"
    with SQLiteOperationCache(path):
        pass
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE operation_cache ADD COLUMN extra TEXT")
    with pytest.raises(CacheSchemaError, match="incompatible layout"):
        SQLiteOperationCache(path)
    with sqlite3.connect(path) as connection:
        assert any(
            row[1] == "extra"
            for row in connection.execute("PRAGMA table_info(operation_cache)")
        )


def test_incompatible_entry_payload_does_not_poison_later_write(tmp_path: Path) -> None:
    path = tmp_path / "bad-entry.db"
    with SQLiteOperationCache(path, clock=FakeClock()) as cache:
        store(cache, KEY_A)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE operation_cache SET result_json = ? WHERE key_digest = ?",
                (
                    '{"format":"operation-result:v2","status":"success",'
                    '"value":{"type":"text","value":"old"},"evidence":[]}',
                    KEY_A,
                ),
            )
        assert cache.get(KEY_A).status == "invalid"
        store(cache, KEY_A, success("new"))
        assert cache.get(KEY_A).result == success("new")


def test_sqlite_operational_error_is_not_misreported_as_cache_miss(tmp_path: Path) -> None:
    cache = SQLiteOperationCache(tmp_path / "closed.db")
    cache.close()
    with pytest.raises(sqlite3.ProgrammingError):
        cache.get(KEY_A)
