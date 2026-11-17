"""The cache codec stores what a web search found for a row, losslessly and strictly."""

from json import dumps, loads
from typing import Any

import pytest

from ai_data_enrichment.execution.models import (
    Evidence,
    FoundRecord,
    FoundValue,
    GeoPoint,
    OperationResult,
)
from ai_data_enrichment.execution.result_codec import (
    RESULT_FORMAT_VERSION,
    deserialize_success,
    serialize_success,
)

PAGE = "https://example.org/everest"
PAGE_2 = "https://example.org/everest/climbs"


def success(record: FoundRecord, *evidence: Evidence) -> OperationResult:
    return OperationResult(status="success", value=record, evidence=evidence)


def round_trip(result: OperationResult) -> OperationResult:
    encoded = serialize_success(result)
    restored = deserialize_success(encoded)
    assert restored == result
    assert serialize_success(restored) == encoded
    return restored


def test_a_number_with_a_unit_a_quote_and_a_link() -> None:
    record = FoundRecord(
        slots={
            "value_1": FoundValue(
                value=8848.86, basis="stated", sources=(PAGE,), quote="8,848.86 m", unit="m"
            )
        }
    )
    restored = round_trip(success(record, Evidence(source="openai_web_search", reference=PAGE)))
    assert isinstance(restored.value, FoundRecord)
    value = restored.value.slots["value_1"].value
    assert type(value) is float and value == 8848.86  # bit for bit, not a rounded text


def test_text_and_yes_no_with_several_links_and_no_quote() -> None:
    record = FoundRecord(
        slots={
            "value_1": FoundValue(value="1953", basis="concluded", sources=(PAGE, PAGE_2)),
            "value_2": FoundValue(value="No", basis="stated", sources=(PAGE_2,)),
        }
    )
    restored = round_trip(
        success(
            record,
            Evidence(source="openai_web_search", reference=PAGE),
            Evidence(source="openai_web_search", reference=PAGE_2),
        )
    )
    assert isinstance(restored.value, FoundRecord)
    first = restored.value.slots["value_1"]
    assert first.sources == (PAGE, PAGE_2) and first.quote is None and first.unit is None
    assert first.basis == "concluded"
    assert [item.reference for item in restored.evidence] == [PAGE, PAGE_2]


def test_a_slot_that_found_nothing_stays_absent_and_keeps_its_unit() -> None:
    record = FoundRecord(
        slots={
            "value_1": FoundValue(value=None, basis=None, unit="m"),
            "value_2": FoundValue(value="x", basis="stated", sources=(PAGE,), quote="x"),
        }
    )
    restored = round_trip(success(record, Evidence(source="openai_web_search", reference=PAGE)))
    assert isinstance(restored.value, FoundRecord)
    empty = restored.value.slots["value_1"]
    assert (empty.value, empty.basis, empty.sources, empty.quote, empty.unit) == (
        None,
        None,
        (),
        None,
        "m",
    )


def test_the_order_of_the_slots_is_kept_even_where_names_sort_differently() -> None:
    names = ["value_2", "value_10", "value_1"]
    record = FoundRecord(
        slots={name: FoundValue(value=name, basis="stated", sources=()) for name in names}
    )
    restored = round_trip(success(record))
    assert isinstance(restored.value, FoundRecord)
    assert list(restored.value.slots) == names


def test_unicode_survives() -> None:
    record = FoundRecord(
        slots={"value_1": FoundValue(value="Говерла 山 🏔", basis="stated", quote="гора")}
    )
    assert "Говерла" in serialize_success(success(record))
    round_trip(success(record))


def test_the_format_string_is_unchanged_and_other_types_read_as_before() -> None:
    # A new value type has a new discriminator; the version stays, as the module says.
    assert RESULT_FORMAT_VERSION == "operation-result:v1"
    older = (
        '{"evidence":[],"format":"operation-result:v1","status":"success",'
        '"value":{"type":"text","value":"Everest"}}'
    )
    assert deserialize_success(older) == OperationResult(status="success", value="Everest")
    point = OperationResult(status="success", value=GeoPoint(latitude=1.0, longitude=2.0))
    assert round_trip(point) == point


def _document() -> dict[str, Any]:
    record = FoundRecord(
        slots={"value_1": FoundValue(value="a", basis="stated", sources=(PAGE,), quote="q")}
    )
    return loads(serialize_success(success(record)))


def _slot(document: dict[str, Any]) -> dict[str, Any]:
    return document["value"]["slots"][0]["found"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["value"].update(slots={}),
        lambda d: d["value"].update(slots="value_1"),
        lambda d: d["value"].update(extra=1),
        lambda d: d["value"]["slots"].append(d["value"]["slots"][0]),  # a repeated name
        lambda d: d["value"]["slots"][0].update(name="not an identifier!"),
        lambda d: d["value"]["slots"][0].update(name=1),
        lambda d: d["value"]["slots"][0].update(extra=1),
        lambda d: _slot(d).pop("unit"),
        lambda d: _slot(d).update(extra=1),
        lambda d: _slot(d).update(basis="guessed"),
        lambda d: _slot(d).update(basis=None),  # a value without a basis
        lambda d: _slot(d).update(sources="https://example.org"),
        lambda d: _slot(d).update(sources=[1]),
        lambda d: _slot(d).update(sources=[" "]),
        lambda d: _slot(d).update(quote=3),
        lambda d: _slot(d).update(quote=" "),
        lambda d: _slot(d).update(unit=""),
        lambda d: _slot(d).update(value={"type": "float", "value": "0.1"}),
        lambda d: _slot(d).update(value={"type": "float", "value": "nan"}),
        lambda d: _slot(d).update(value={"type": "quantity", "value": "0x1.0p+1", "unit": "m"}),
        lambda d: _slot(d).update(value="a"),  # a bare string is not a typed value
        lambda d: _slot(d).update(value={"type": "text", "value": " "}),
        lambda d: _slot(d).update(value=None),  # no value but a basis
    ],
)
def test_a_damaged_found_record_is_rejected(mutate: Any) -> None:
    document = _document()
    mutate(document)
    with pytest.raises(ValueError):
        deserialize_success(dumps(document))


def test_a_reader_without_the_type_would_reject_it_and_this_one_rejects_unknown_types() -> None:
    document = _document()
    document["value"]["type"] = "found_record_v2"
    with pytest.raises(ValueError, match="Unsupported"):
        deserialize_success(dumps(document))
