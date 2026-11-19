"""The capability handler of the text extraction: every outcome as a result of a step."""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from tests.support.text_extraction import TEXT, answer, entry, good_answer, mixed_task, response
from tests.support.web_lookup import FakeApi, StatusError

from ai_data_enrichment.execution.models import FoundRecord, FoundValue, OperationResult
from ai_data_enrichment.execution.result_codec import deserialize_success, serialize_success
from ai_data_enrichment.providers.text_extraction import ExtractionUsage
from ai_data_enrichment.providers.text_extraction_handler import TextExtractionHandler

SENTINEL = "SENTINEL-MAIL-TEXT"


def handler(api: FakeApi, **options: Any) -> tuple[TextExtractionHandler, ExtractionUsage]:
    usage = ExtractionUsage()
    return (
        TextExtractionHandler(api, model="m", reasoning_effort=None, usage=usage, **options),
        usage,
    )


def inputs(**changes: Any) -> dict[str, Any]:
    return {"task": mixed_task().to_json(), "text": TEXT, **changes}


def failure(result: OperationResult, code: str, message: str | None = None) -> None:
    assert result.status == "failure" and result.value is None and result.evidence == ()
    assert result.error_code == code
    if message is not None:
        assert result.message == message
    assert result.message and SENTINEL not in result.message


# --- found --------------------------------------------------------------------------------


def test_a_found_text_is_a_record_with_a_value_a_mark_and_a_quote_for_each_slot() -> None:
    api = FakeApi(response(good_answer()))
    result = handler(api)[0].execute(inputs=inputs(), parameters={})
    assert result.status == "success" and result.error_code is None and result.evidence == ()
    record = result.value
    assert isinstance(record, FoundRecord)
    assert list(record.slots) == [f"value_{i}" for i in range(1, 6)]
    assert [(s.value, s.basis, s.quote) for s in record.slots.values()] == [
        ("Cloudbox Plus", "literal", "Cloudbox Plus, monthly."),
        (9.99, "literal", "Total: 9,99 EUR"),
        ("2026-11-03", "literal", "Paid on 3 Nov 2026"),
        ("Yes", "derived", "Renews on 01.12.2026"),
        ("payment", "derived", "Total: 9,99 EUR"),
    ]
    assert all(s.sources == () and s.unit is None for s in record.slots.values())
    # A number is a float, a date is text: what the workbook writer takes.
    assert type(record.slots["value_2"].value) is float
    assert type(record.slots["value_3"].value) is str
    assert len(api.calls) == 1


def test_the_request_made_is_the_one_of_the_provider_and_the_usage_is_counted() -> None:
    api = FakeApi(response(good_answer()))
    step, usage = handler(api)
    step.execute(inputs=inputs(), parameters={})
    [call] = api.calls
    assert call["model"] == "m" and call["store"] is False and call["timeout"] == 60.0
    assert "tools" not in call and TEXT in call["input"]
    totals = usage.totals()
    assert (totals.requests, totals.input_tokens, totals.output_tokens) == (1, 700, 90)
    assert step.configuration_id == "openai-text-extraction-p1-m-default"


def test_a_slot_with_no_kept_value_is_in_the_record_as_not_found() -> None:
    reply = answer(value_2=entry(9.99, "Total: 9,99 EUR"), value_1=entry("X", "not in the text"))
    result = handler(FakeApi(response(reply)))[0].execute(inputs=inputs(), parameters={})
    assert isinstance(result.value, FoundRecord)
    slots = result.value.slots
    assert list(slots) == [f"value_{i}" for i in range(1, 6)]
    assert slots["value_2"].value == 9.99
    for name in ("value_1", "value_3", "value_4", "value_5"):
        assert slots[name] == FoundValue(value=None, basis=None)


# --- nothing found and not applicable ---------------------------------------------------


def test_nothing_found_is_reported_as_the_web_lookup_reports_its_own() -> None:
    api = FakeApi(response(answer(applies=True)))
    result = handler(api)[0].execute(inputs=inputs(), parameters={})
    failure(result, "NOTHING_FOUND", "The text holds no value that can be traced for any column.")
    # Everything dropped is nothing found too.
    api = FakeApi(response(answer(value_1=entry("X", "not in the text"))))
    failure(handler(api)[0].execute(inputs=inputs(), parameters={}), "NOTHING_FOUND")


def test_not_applicable_is_a_failed_step_and_never_a_record() -> None:
    api = FakeApi(response({**good_answer(), "applies": False}))
    result = handler(api)[0].execute(inputs=inputs(), parameters={})
    failure(result, "NOT_APPLICABLE", "The text is not about the task.")
    assert result.value is None  # an empty record is the cache's sign for "nothing found"


# --- the input ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task",
    [None, 1.0, "", "not json", '{"question":"q","columns":[]}', mixed_task().to_json() + " "],
)
def test_a_missing_or_invalid_task_is_invalid_input_and_nothing_is_asked(task: Any) -> None:
    api = FakeApi(response(good_answer()))
    given: dict[str, Any] = {"text": TEXT} if task is None else {"task": task, "text": TEXT}
    result = handler(api)[0].execute(inputs=given, parameters={})
    failure(result, "INVALID_INPUT", "The extraction task is missing or not valid.")
    assert api.calls == []


@pytest.mark.parametrize("text", [None, "", "   ", "\n\t  \n"])
def test_a_missing_or_blank_text_is_a_missing_value_and_nothing_is_asked(text: Any) -> None:
    api = FakeApi(response(good_answer()))
    given = {"task": mixed_task().to_json()} if text is None else inputs(text=text)
    result = handler(api)[0].execute(inputs=given, parameters={})
    failure(result, "MISSING_VALUE", "The row has no text to read.")
    assert api.calls == []


def test_a_text_that_is_not_text_is_invalid_input() -> None:
    api = FakeApi(response(good_answer()))
    result = handler(api)[0].execute(inputs=inputs(text=4.5), parameters={})
    failure(result, "INVALID_INPUT", "The text to read must be text.")
    assert api.calls == []


# --- failures of the request and of the reply -------------------------------------------


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (StatusError(401), "SOURCE_ACCESS_DENIED"),
        (StatusError(403), "SOURCE_ACCESS_DENIED"),
        (StatusError(400), "REQUEST_REFUSED"),
        (StatusError(429), "PROVIDER_ERROR"),
        (StatusError(500), "PROVIDER_ERROR"),
        (TimeoutError(SENTINEL), "PROVIDER_ERROR"),
        (response(good_answer(), status="incomplete"), "RESPONSE_INCOMPLETE"),
        (response(f"{SENTINEL} {{not json"), "INVALID_RESPONSE"),
        (response({"applies": True}), "INVALID_RESPONSE"),
        (response(good_answer(), refusal=True), "INVALID_RESPONSE"),
    ],
)
def test_every_other_failure_keeps_its_code_and_a_fixed_message(reply: Any, code: str) -> None:
    api = FakeApi(reply)
    result = handler(api)[0].execute(inputs=inputs(text=f"{TEXT}\n{SENTINEL}"), parameters={})
    failure(result, code)
    assert "secret body" not in str(result.message)


def test_a_failed_request_is_counted_as_a_request() -> None:
    step, usage = handler(FakeApi(StatusError(500)))
    step.execute(inputs=inputs(), parameters={})
    assert usage.totals().requests == 1


# --- the deadline ------------------------------------------------------------------------


def test_no_request_is_started_that_could_end_after_the_deadline() -> None:
    now = [100.0]
    api = FakeApi(response(good_answer()))
    step, usage = handler(api, timeout_seconds=30.0, clock=lambda: now[0], deadline=140.0)
    assert step.execute(inputs=inputs(), parameters={}).status == "success"
    now[0] = 110.1  # 110.1 + 30 > 140
    result = step.execute(inputs=inputs(), parameters={})
    failure(result, "DEADLINE_REACHED", "The time for the extraction run is used up.")
    assert len(api.calls) == 1 and usage.totals().requests == 1


# --- what a record may say -----------------------------------------------------------------


def test_a_found_value_may_be_literal_or_derived_and_keeps_every_other_rule() -> None:
    for basis in ("stated", "concluded", "literal", "derived"):
        assert FoundValue(value="x", basis=basis, quote="x").basis == basis  # type: ignore[arg-type]
    for bad in (
        {"value": None, "basis": "literal"},
        {"value": "x", "basis": None},
        {"value": "x", "basis": "verified"},
        {"value": "x", "basis": "derived", "quote": " "},
        {"value": " ", "basis": "literal"},
        {"value": float("nan"), "basis": "literal"},
    ):
        with pytest.raises(ValidationError):
            FoundValue.model_validate(bad)


def test_an_extracted_record_round_trips_through_the_cache_codec_and_json() -> None:
    result = handler(FakeApi(response(good_answer())))[0].execute(inputs=inputs(), parameters={})
    assert deserialize_success(serialize_success(result)) == result
    assert OperationResult.model_validate_json(result.model_dump_json()) == result
    stored = json.loads(serialize_success(result))
    assert stored["evidence"] == []
    slots = {item["name"]: item["found"] for item in stored["value"]["slots"]}
    assert (slots["value_2"]["basis"], slots["value_2"]["quote"]) == ("literal", "Total: 9,99 EUR")
    assert slots["value_2"]["sources"] == []


def test_a_cache_entry_with_another_basis_is_still_refused() -> None:
    result = handler(FakeApi(response(good_answer())))[0].execute(inputs=inputs(), parameters={})
    stored = serialize_success(result).replace('"basis":"literal"', '"basis":"verified"', 1)
    with pytest.raises(ValueError, match="Cached basis"):
        deserialize_success(stored)


def test_a_value_that_is_not_kept_is_counted_by_its_reason_code_only() -> None:
    reply = answer(
        value_1=entry("Cloudbox Plus", "Cloudbox Pro, monthly."),  # not in the text
        value_2=entry(9.99, "Total: 9,99 EUR"),
        value_3=entry("2025-12-30", "Paid on 3 Nov 2026"),  # neither the day nor the year is quoted
    )
    handler_, usage = handler(FakeApi(response(reply)))
    assert handler_.execute(inputs=inputs(), parameters={}).status == "success"
    assert usage.dropped() == {"quote_not_in_text": 1, "date_not_in_quote": 1}
    # Nothing but the codes is kept; a text that is not about the task drops nothing.
    handler_.execute(inputs=inputs(text=TEXT + " "), parameters={})
    other, other_usage = handler(FakeApi(response(answer(applies=False))))
    other.execute(inputs=inputs(), parameters={})
    assert other_usage.dropped() == {} and usage.dropped()["quote_not_in_text"] == 2
