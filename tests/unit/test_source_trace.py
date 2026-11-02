"""Deterministic source-trace tests; no external endpoints are contacted."""

import pytest

from ai_data_enrichment.evaluation.smoke import (
    OfflineGeocodeTransport,
    OfflineWikidataTransport,
    run_smoke,
)
from ai_data_enrichment.evaluation.source_trace import SourceTraceTransport
from ai_data_enrichment.providers.open_meteo import GEOCODING_URL
from ai_data_enrichment.providers.wikidata_constants import WIKIDATA_API_URL


def test_geocode_trace_preserves_payload_and_reports_candidates() -> None:
    transport = SourceTraceTransport(OfflineGeocodeTransport())
    response = transport.get_json(
        url=GEOCODING_URL,
        params={"name": "Berlin", "countryCode": "DE"},
        timeout_seconds=10.0,
    )
    assert isinstance(response, dict)
    assert response["results"][0]["name"] == "Berlin"
    assert len(transport.events) == 1
    event = transport.events[0]
    assert event["provider"] == "open-meteo"
    assert event["result_count"] == 1
    candidates = event["candidates"]
    assert isinstance(candidates, list)
    assert candidates[0]["id"] == 2950159


def test_wikidata_trace_reveals_entity_and_class_proof_without_full_response() -> None:
    transport = SourceTraceTransport(OfflineWikidataTransport())
    search = transport.get_json(
        url=WIKIDATA_API_URL,
        params={"action": "wbsearchentities", "search": "Mount Everest"},
        timeout_seconds=10.0,
    )
    entity = transport.get_json(
        url=WIKIDATA_API_URL,
        params={"action": "wbgetentities", "ids": "Q513"},
        timeout_seconds=10.0,
    )
    assert isinstance(search, dict) and isinstance(entity, dict)
    assert len(transport.events) == 2
    assert transport.events[0]["candidates"] == [
        {"id": "Q513", "label": "Mount Everest", "match": None}
    ]
    entities = transport.events[1]["entities"]
    assert isinstance(entities, list)
    assert entities[0]["P31"] == [{"id": "Q8502", "rank": "normal"}]
    assert entities[0]["P17"] == [{"id": "Q837", "rank": "normal"}]
    assert "claims" not in str(transport.events)


def test_nonlive_diagnostic_mode_cannot_enable_network() -> None:
    for mode in ("offline", "cache"):
        with pytest.raises(ValueError, match="only available in live mode"):
            run_smoke("mountains", mode, diagnose=True)
