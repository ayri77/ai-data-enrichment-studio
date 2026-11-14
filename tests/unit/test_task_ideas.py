"""Task ideas proposed by a model: text for the instruction field, checked before use."""

import json

import pytest

from ai_data_enrichment.domain.dataset_profile import ColumnProfile, DatasetProfile, PrimitiveType
from ai_data_enrichment.plan_run_worker import builtin_registry
from ai_data_enrichment.task_ideas import (
    IDEAS_SCHEMA,
    MAX_CHOSEN_IDEA_LENGTH,
    MAX_IDEA_LENGTH,
    MAX_TASK_IDEAS,
    Proposal,
    TaskIdeaError,
    idea_messages,
    parse_ideas,
    propose_tasks,
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


def test_several_chosen_columns_get_a_prompt_of_their_own_and_one_task() -> None:
    registry = builtin_registry()
    chosen = ["Висота", "Нова колонка"]
    system, user = idea_messages(PROFILE, registry, "uk", chosen)
    # The chosen headers travel as data; a header the table lacks is a new column.
    assert json.loads(user["content"])["fill_columns"] == chosen
    # Not the open question with a rule added: that rule lost against "name the
    # column to fill" on a live model.
    assert "Write exactly one task" in system["content"]
    assert "prefer filling a column that is empty" not in system["content"]
    assert f"at most {MAX_TASK_IDEAS} tasks" not in system["content"]
    both = "Запиши у «Висота» висоту гори, а у «Нова колонка» відношення."
    client = Client({"tasks": [both, "Заповни «Висота» висотою гори."]})
    tasks = suggest_tasks(
        client, profile=PROFILE, registry=registry, language="uk", fill_columns=chosen
    )
    assert tasks == (both,)
    messages, schema = client.calls[0]
    assert json.loads(messages[1]["content"])["fill_columns"] == chosen
    assert schema["properties"]["tasks"]["maxItems"] == 1


@pytest.mark.parametrize(
    "answer",
    [
        # What a live model returned: a task for the first chosen column only.
        ["Заповни «Висота» висотою гори."],
        ["Заповни «Нова колонка» відношенням.", "Заповни «Висота» висотою гори."],
        # The header is written otherwise than in the table.
        ["Запиши у «висота» висоту, а у «Нова колонка» відношення."],
        [],
    ],
)
def test_a_task_that_leaves_a_chosen_column_out_is_not_proposed(answer) -> None:
    tasks = suggest_tasks(
        Client({"tasks": answer}),
        profile=PROFILE,
        registry=builtin_registry(),
        language="uk",
        fill_columns=["Висота", "Нова колонка"],
    )
    # A plan from it would be refused for the column it does not fill.
    assert tasks == ()


def test_a_later_task_that_names_every_chosen_column_is_the_one_kept() -> None:
    both = "У «Нова колонка» — відношення, у «Висота» — висота гори."
    tasks = suggest_tasks(
        Client({"tasks": ["Заповни «Висота» висотою гори.", both, both + " Ще раз."]}),
        profile=PROFILE,
        registry=builtin_registry(),
        language="uk",
        fill_columns=["Висота", "Нова колонка"],
    )
    assert tasks == (both,)


@pytest.mark.parametrize("chosen", [[], ["Висота"]])
def test_one_chosen_column_or_none_asks_the_open_question(chosen) -> None:
    _, user = idea_messages(PROFILE, builtin_registry(), "uk", chosen)
    assert "fill_columns" not in json.loads(user["content"])
    client = Client({"tasks": ["One.", "Two.", "Three.", "Four."]})
    tasks = suggest_tasks(
        client, profile=PROFILE, registry=builtin_registry(), language="uk", fill_columns=chosen
    )
    assert len(tasks) == MAX_TASK_IDEAS


def test_one_task_for_three_columns_may_be_longer_than_a_one_column_proposal() -> None:
    """A task that says what goes into each of three columns and from which columns it
    is read does not fit 400 characters; with that bound a complete task was dropped
    and the interface said that none was proposed (seen live, 2026-11-14)."""
    chosen = ["distance", "road", "detour"]
    long_task = (
        "Для кожної пари міст із колонок «Capital_From» (країна «Country_From») і "
        "«Capital_To» (країна «Country_To») запишіть у колонку «distance» відстань по "
        "прямій у кілометрах, у колонку «road» — відстань автомобільними дорогами в "
        "кілометрах, а в колонку «detour» — у скільки разів відстань дорогами більша за "
        "відстань по прямій. Якщо місто не вдалося визначити однозначно, залиште всі три "
        "клітинки цього рядка порожніми й нічого не вигадуйте."
    )
    assert MAX_IDEA_LENGTH < len(long_task) < MAX_CHOSEN_IDEA_LENGTH
    proposal = propose_tasks(
        Client({"tasks": [long_task]}),
        profile=PROFILE,
        registry=builtin_registry(),
        language="uk",
        fill_columns=chosen,
    )
    assert proposal == Proposal((long_task,), proposed=1)
    # The open question keeps its short bound.
    open_question = propose_tasks(
        Client({"tasks": [long_task, "Short one."]}),
        profile=PROFILE,
        registry=builtin_registry(),
        language="uk",
    )
    assert open_question == Proposal(("Short one.",), proposed=2, too_long=1)


def test_what_became_of_the_reply_is_counted_without_keeping_its_text() -> None:
    chosen = ["Висота", "Нова колонка"]

    def outcome(tasks: list[object]) -> Proposal:
        return propose_tasks(
            Client({"tasks": tasks}),
            profile=PROFILE,
            registry=builtin_registry(),
            language="uk",
            fill_columns=chosen,
        )

    assert outcome([]) == Proposal(())
    assert outcome(["Заповни «Висота»."]) == Proposal((), proposed=1, incomplete=1)
    assert outcome(["x" * (MAX_CHOSEN_IDEA_LENGTH + 1)]) == Proposal((), proposed=1, too_long=1)
    assert outcome([7, "", "  "]) == Proposal(())


def test_an_unclear_header_is_no_reason_to_propose_nothing() -> None:
    system, _ = idea_messages(PROFILE, builtin_registry(), "uk", ["a", "b"])
    assert "A header that does not say what it should hold still gets one" in system["content"]
    assert "Keep the task under 900 characters" in system["content"]
