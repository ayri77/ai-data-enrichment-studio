"""The population and timezone of a found place ride along with its record."""

import json
from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from ai_data_enrichment.execution.models import GeoPoint, OperationResult
from ai_data_enrichment.execution.result_codec import deserialize_success, serialize_success
from ai_data_enrichment.output.value_projection import field_is_empty, project_operation_field
from ai_data_enrichment.providers.open_meteo import GEOCODING_URL, OpenMeteoGeocodingHandler
from ai_data_enrichment.providers.open_meteo_candidates import place_facts


class _Transport:
    def __init__(self, record: dict[str, object]) -> None:
        self.record = record
        self.requests = 0

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        assert url == GEOCODING_URL
        self.requests += 1
        return {"results": [self.record]}


RECORD: dict[str, object] = {
    "id": 2950159,
    "name": "Berlin",
    "country_code": "DE",
    "latitude": 52.52437,
    "longitude": 13.41053,
    "population": 3426354,
    "timezone": "Europe/Berlin",
}


def _geocode(record: dict[str, object]) -> tuple[OperationResult, int]:
    transport = _Transport(record)
    result = OpenMeteoGeocodingHandler(transport).execute(
        inputs={"city": "Berlin", "country": "Germany"}, parameters={}
    )
    return result, transport.requests


def test_the_direct_lookup_keeps_the_facts_of_the_record_with_one_request() -> None:
    result, requests = _geocode(RECORD)
    assert requests == 1
    assert result.value == GeoPoint(
        latitude=52.52437,
        longitude=13.41053,
        population=3426354,
        timezone="Europe/Berlin",
    )


def test_a_fact_the_source_does_not_give_is_none_and_never_fails_the_lookup() -> None:
    without = {k: v for k, v in RECORD.items() if k not in {"population", "timezone"}}
    result, _ = _geocode(without)
    assert result.status == "success"
    assert isinstance(result.value, GeoPoint)
    assert (result.value.population, result.value.timezone) == (None, None)


def test_values_of_another_type_than_documented_are_left_out() -> None:
    assert place_facts({"population": "many", "timezone": " "}) == {}
    assert place_facts({"population": True, "timezone": 3}) == {}
    assert place_facts({"population": -1}) == {}
    # Elevation is deliberately not carried, even when the source gives it.
    assert place_facts({"elevation": 74.0, "population": 0}) == {"population": 0}


def test_a_malformed_fact_does_not_change_which_record_is_accepted() -> None:
    result, _ = _geocode({**RECORD, "population": "many", "timezone": 3})
    assert result.status == "success"
    assert result.value == GeoPoint(latitude=52.52437, longitude=13.41053)


def test_the_cache_codec_round_trips_the_facts() -> None:
    point = GeoPoint(latitude=1.5, longitude=2.5, population=7, timezone="UTC")
    result = OperationResult(status="success", value=point)
    assert deserialize_success(serialize_success(result)).value == point
    bare = OperationResult(status="success", value=GeoPoint(latitude=1.5, longitude=2.5))
    assert deserialize_success(serialize_success(bare)).value == bare.value


def test_an_entry_written_before_the_facts_existed_is_rejected_not_read_as_empty() -> None:
    payload = json.loads(
        serialize_success(
            OperationResult(status="success", value=GeoPoint(latitude=1.5, longitude=2.5))
        )
    )
    for name in ("population", "timezone"):
        del payload["value"][name]
    with pytest.raises(ValueError, match="object fields"):
        deserialize_success(json.dumps(payload))


def test_the_value_object_rejects_impossible_facts() -> None:
    point = {"latitude": 0.0, "longitude": 0.0}
    impossible = [{"population": -1}, {"population": 1.5}]
    for bad in [*impossible, {"timezone": " "}]:
        with pytest.raises(ValidationError):
            GeoPoint.model_validate({**point, **bad})


def test_a_count_is_projected_as_a_number_and_a_missing_field_is_empty() -> None:
    point = GeoPoint(latitude=1.5, longitude=2.5, population=7)
    result = OperationResult(status="success", value=point)
    projected = project_operation_field(result, "population")
    assert projected == 7.0 and type(projected) is float
    assert field_is_empty(result, "timezone") and not field_is_empty(result, "population")
    # A name the value does not declare is not a gap: it stays an error.
    assert not field_is_empty(result, "altitude")
    with pytest.raises(ValueError, match="Unsupported output field: altitude"):
        project_operation_field(result, "altitude")
