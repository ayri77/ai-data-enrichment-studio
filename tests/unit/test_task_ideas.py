"""Task ideas proposed by a model: text for the instruction field, checked before use."""

import json

import pytest

from ai_data_enrichment.domain.dataset_profile import ColumnProfile, DatasetProfile, PrimitiveType
from ai_data_enrichment.plan_run_worker import builtin_registry
from ai_data_enrichment.task_ideas import (
    IDEAS_SCHEMA,
    MAX_TASK_IDEAS,
    TaskIdeaError,
    idea_messages,
    parse_ideas,
    suggest_tasks,
)

PROFILE = DatasetProfile(
    dataset_id="source",
    name="Sheet1",
    row_count=10,
    columns=[
        ColumnProfile(name="Місто", inferred_type=PrimitiveType.TEXT, null_count=0),
        ColumnProfile(name="Країна", inferred_type=PrimitiveType.TEXT, null_count=0),
        ColumnProfile(name="Висота", inferred_type=PrimitiveType.UNKNOWN, null_count=10),
    ],
    sample_records=[{"Місто": "Київ", "Країна": "Україна", "Висота": None}],
)


class Client:
    def __init__(self, reply: object = None, error: Exception | None = None) -> None:
        self.reply, self.error, self.calls = reply, error, []

    def complete(self, *, messages, response_schema) -> str:
        self.calls.append((messages, response_schema))
        if self.error:
            raise self.error
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def test_what_is_sent_is_the_planning_profile_the_capabilities_and_the_language() -> None:
    registry = builtin_registry()
    system, user = idea_messages(PROFILE, registry, "uk")
    assert system["role"] == "system" and "data" in system["content"]
    sent = json.loads(user["content"])
    assert sent["language"] == "Ukrainian"
    assert sent["rows"] == 10
    assert sent["columns"] == [
        {"name": "Місто", "type": "Text", "empty_cells": 0},
        {"name": "Країна", "type": "Text", "empty_cells": 0},
        {"name": "Висота", "type": "Unknown", "empty_cells": 10},
    ]
    assert sent["sample_rows"] == PROFILE.sample_records
    # The model is told what the tool can do, from the same registry planning uses.
    assert sent["capabilities"] == registry.describe_all()
    assert set(sent) == {"language", "capabilities", "rows", "columns", "sample_rows"}


def test_the_model_is_asked_once_for_a_list_of_tasks() -> None:
    client = Client({"tasks": ["Знайди висоту кожної гори й запиши у «Висота»."]})
    ideas = suggest_tasks(client, profile=PROFILE, registry=builtin_registry(), language="uk")
    assert ideas == ("Знайди висоту кожної гори й запиши у «Висота».",)
    [(messages, schema)] = client.calls
    assert schema is IDEAS_SCHEMA and len(messages) == 2


def test_entries_that_are_not_a_task_are_dropped() -> None:
    reply = {
        "tasks": [
            "  Fill   the height.  ",
            "fill the height.",  # A repeat.
            "",
            7,
            None,
            "x" * 401,
            "Bad\u0000text",
            "Find the distance.",
            "A third one.",
            "A fourth one.",
        ]
    }
    ideas = parse_ideas(json.dumps(reply))
    assert ideas == ("Fill the height.", "Find the distance.", "A third one.")
    assert len(ideas) == MAX_TASK_IDEAS
    assert parse_ideas('{"tasks": []}') == ()


@pytest.mark.parametrize("reply", ["not json", "[]", "{}", '{"tasks": "one"}', "null"])
def test_a_reply_of_another_shape_is_an_error_not_an_empty_list(reply) -> None:
    with pytest.raises(TaskIdeaError):
        suggest_tasks(Client(reply), profile=PROFILE, registry=builtin_registry(), language="en")


def test_a_model_failure_is_an_error() -> None:
    with pytest.raises(TaskIdeaError):
        suggest_tasks(
            Client(error=RuntimeError("timeout")),
            profile=PROFILE,
            registry=builtin_registry(),
            language="en",
        )


def test_several_chosen_columns_are_stated_and_one_task_is_kept() -> None:
    registry = builtin_registry()
    chosen = ["Висота", "Нова колонка"]
    system, user = idea_messages(PROFILE, registry, "uk", chosen)
    # The chosen headers travel as data; a header the table lacks is a new column.
    assert json.loads(user["content"])["fill_columns"] == chosen
    assert "exactly one task" in system["content"]
    client = Client({"tasks": ["Fill both columns.", "Fill only the height."]})
    tasks = suggest_tasks(
        client, profile=PROFILE, registry=registry, language="uk", fill_columns=chosen
    )
    # Alternatives would each leave a chosen column out; only the first is kept.
    assert tasks == ("Fill both columns.",)
    assert json.loads(client.calls[0][0][1]["content"])["fill_columns"] == chosen


@pytest.mark.parametrize("chosen", [[], ["Висота"]])
def test_one_chosen_column_or_none_asks_the_open_question(chosen) -> None:
    _, user = idea_messages(PROFILE, builtin_registry(), "uk", chosen)
    assert "fill_columns" not in json.loads(user["content"])
    client = Client({"tasks": ["One.", "Two.", "Three.", "Four."]})
    tasks = suggest_tasks(
        client, profile=PROFILE, registry=builtin_registry(), language="uk", fill_columns=chosen
    )
    assert len(tasks) == MAX_TASK_IDEAS
