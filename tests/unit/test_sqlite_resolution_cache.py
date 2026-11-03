"""Offline SQLite paired result/report cache tests with strict provenance."""

import sqlite3
from pathlib import Path

import pytest
from tests.support.http import StubJsonTransport

from ai_data_enrichment.execution.models import Evidence, GeoPoint, OperationResult
from ai_data_enrichment.execution.sqlite_cache import SQLiteOperationCache
from ai_data_enrichment.providers.open_meteo_resolution import OpenMeteoResolutionService
from ai_data_enrichment.resolution.models import ResolutionPolicy
from ai_data_enrichment.resolution.sqlite_cache import (
    ResolutionCacheHit,
    SQLiteResolutionCache,
    validate_paired_result,
)

_DIGEST = "a" * 64


class Clock:
    def __init__(self, instant: float = 1000.0) -> None:
        self.instant = instant

    def __call__(self) -> float:
        return self.instant


def _pair() -> ResolutionCacheHit:
    transport = StubJsonTransport(
        {
            "results": [
                {
                    "id": 2950159,
                    "name": "Berlin",
                    "country_code": "DE",
                    "admin1": "State of Berlin",
                    "feature_code": "PPLC",
                    "latitude": 52.52437,
                    "longitude": 13.41053,
                }
            ]
        }
    )
    outcome = OpenMeteoResolutionService(transport).resolve(
        inputs={"city": "Berlin", "country": "Germany"},
        policy=ResolutionPolicy(policy_id="strict", version="1"),
    )
    assert outcome.operation.status == "success"
    return ResolutionCacheHit(operation=outcome.operation, report=outcome.report)


def test_resolution_cache_persists_complete_provenance_across_reopens(tmp_path: Path) -> None:
    path = tmp_path / "paired.db"
    pair = _pair()
    with SQLiteResolutionCache(path, clock=Clock()) as cache:
        assert cache.get(_DIGEST, provider_id="open-meteo").status == "miss"
        cache.put(_DIGEST, pair, provider_id="open-meteo", ttl_seconds=50)
        assert cache.get(_DIGEST, provider_id="open-meteo").value == pair
    with SQLiteResolutionCache(path, clock=Clock()) as reopened:
        hit = reopened.get(_DIGEST, provider_id="open-meteo")
        assert hit.status == "hit"
        assert hit.value == pair
        assert hit.value is not None
        assert hit.value.report.decision.selected is not None
        assert hit.value.report.decision.selected.entity_id == "2950159"
        assert hit.value.report.candidate_set.candidates[0].evidence == pair.operation.evidence


def test_ttl_is_explicit_and_expired_results_are_deleted(tmp_path: Path) -> None:
    clock = Clock()
    with SQLiteResolutionCache(tmp_path / "ttl.db", clock=clock) as cache:
        cache.put(_DIGEST, _pair(), provider_id="open-meteo", ttl_seconds=10)
        clock.instant = 1009.999
        assert cache.get(_DIGEST, provider_id="open-meteo").status == "hit"
        clock.instant = 1010.0
        assert cache.get(_DIGEST, provider_id="open-meteo").status == "expired"
        assert cache.get(_DIGEST, provider_id="open-meteo").status == "miss"


def test_corrupt_or_mismatched_paired_report_is_invalidated(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.db"
    with SQLiteResolutionCache(path, clock=Clock()) as cache:
        cache.put(_DIGEST, _pair(), provider_id="open-meteo", ttl_seconds=30)
        with sqlite3.connect(path) as con:
            con.execute(
                "UPDATE resolution_cache SET payload_json = ? WHERE key_digest = ?",
                ('{"format":"broken"}', _DIGEST),
            )
        assert cache.get(_DIGEST, provider_id="open-meteo").status == "invalid"
        assert cache.get(_DIGEST, provider_id="open-meteo").status == "miss"


def test_stored_provider_mismatch_is_not_reused(tmp_path: Path) -> None:
    with SQLiteResolutionCache(tmp_path / "provider.db", clock=Clock()) as cache:
        cache.put(_DIGEST, _pair(), provider_id="open-meteo", ttl_seconds=30)
        assert cache.get(_DIGEST, provider_id="wikidata").status == "invalid"
        assert cache.get(_DIGEST, provider_id="open-meteo").status == "miss"


def test_legacy_sqlite_schema_cannot_be_repurposed_for_audited_cache(tmp_path: Path) -> None:
    path = tmp_path / "ordinary.db"
    with SQLiteOperationCache(path):
        pass
    with pytest.raises(ValueError, match="separate file"):
        SQLiteResolutionCache(path)


def test_never_cache_failed_or_unverified_resolution() -> None:
    pair = _pair()
    failure = OperationResult(status="failure", error_code="AMBIGUOUS", message="Need user review.")
    with pytest.raises(ValueError, match="Only an audited"):
        validate_paired_result(failure, pair.report, provider_id="open-meteo")


def test_wrong_evidence_or_provider_cannot_pass_pair_validation() -> None:
    pair = _pair()
    fake = OperationResult(
        status="success",
        value=pair.operation.value,
        evidence=(Evidence(source="other", reference="fabricated"),),
    )
    with pytest.raises(ValueError, match="Evidence"):
        validate_paired_result(fake, pair.report, provider_id="open-meteo")
    with pytest.raises(ValueError, match="provider"):
        validate_paired_result(pair.operation, pair.report, provider_id="wikidata")


def test_cached_geographic_coordinates_must_match_verified_selected_candidate() -> None:
    pair = _pair()
    changed = OperationResult(
        status="success",
        value=GeoPoint(latitude=0.0, longitude=0.0),
        evidence=pair.operation.evidence,
    )
    with pytest.raises(ValueError, match="coordinates"):
        validate_paired_result(changed, pair.report, provider_id="open-meteo")


@pytest.mark.parametrize("digest", ["a", "Z" * 64, "a" * 63 + "g"])
def test_invalid_key_digest_fails_closed(tmp_path: Path, digest: str) -> None:
    with SQLiteResolutionCache(tmp_path / "key.db") as cache:
        with pytest.raises(ValueError, match="SHA-256"):
            cache.get(digest, provider_id="open-meteo")
        with pytest.raises(ValueError, match="SHA-256"):
            cache.put(digest, _pair(), provider_id="open-meteo", ttl_seconds=30)


@pytest.mark.parametrize("ttl", [0.0, -1.0, float("inf"), float("nan")])
def test_ttl_must_be_finite_positive(tmp_path: Path, ttl: float) -> None:
    with SQLiteResolutionCache(tmp_path / "bad_ttl.db") as cache:
        with pytest.raises(ValueError, match="TTL"):
            cache.put(_DIGEST, _pair(), provider_id="open-meteo", ttl_seconds=ttl)


def test_unversioned_unrelated_database_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "foreign.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE foreign_table (a TEXT)")
    with pytest.raises(ValueError, match="separate file"):
        SQLiteResolutionCache(path)


def test_invalid_schema_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    with SQLiteResolutionCache(path):
        pass
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA user_version = 999")
    with pytest.raises(ValueError, match="separate file"):
        SQLiteResolutionCache(path)
