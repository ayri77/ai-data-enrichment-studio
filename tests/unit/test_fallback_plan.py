"""The fallback request, the one-step plan built from it and the registry that knows it."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from ai_data_enrichment.capabilities.definitions import offered_capabilities
from ai_data_enrichment.capabilities.fallback import (
    FallbackPlanError,
    FallbackRequest,
    FallbackTarget,
    build_fallback_plan,
    fallback_registry,
    is_fallback_plan,
    web_lookup_spec,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.capability import DataType
from ai_data_enrichment.domain.dataset_profile import ColumnProfile, DatasetProfile, PrimitiveType
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.plan_run import MAX_PLAN_BYTES
from ai_data_enrichment.providers.web_lookup import MAX_TASK_JSON, LookupColumn, LookupTask
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy, PlanValidator

T, N = PrimitiveType.TEXT, PrimitiveType.NUMBER


def table(**types: PrimitiveType) -> list[ColumnProfile]:
    return [
        ColumnProfile(name=name, inferred_type=kind, null_count=0) for name, kind in types.items()
    ]


COLUMNS = table(
    Mountain=T,
    Country=T,
    Rank=PrimitiveType.INTEGER,
    Notes=T,
    Score=N,
    Born=PrimitiveType.DATE,
    Flag=PrimitiveType.BOOLEAN,
    Odd=PrimitiveType.MIXED,
    Blank=PrimitiveType.UNKNOWN,
)


def request(**changes: Any) -> FallbackRequest:
    base: dict[str, Any] = {
        "question": "Find facts about this mountain.",
        "context_columns": ("Mountain", "Country"),
        "targets": (
            FallbackTarget(column="Height", kind="number", unit="m"),
            FallbackTarget(column="First ascent", kind="text"),
            FallbackTarget(column="Climbed in winter", kind="yes_no"),
        ),
    }
    return FallbackRequest(**{**base, **changes})


def target(column: str, kind: str = "text", unit: str | None = None) -> FallbackTarget:
    return FallbackTarget(column=column, kind=kind, unit=unit)  # type: ignore[arg-type]


# --- FallbackRequest -----------------------------------------------------------------


def test_a_request_keeps_what_the_user_chose() -> None:
    value = request(question="  Who? ")
    assert value.question == "Who?"
    assert value.context_columns == ("Mountain", "Country")
    assert [t.kind for t in value.targets] == ["number", "text", "yes_no"]
    assert (
        value.to_task().to_json()
        == LookupTask(
            question="Who?",
            context=(("ctx_1", "Mountain"), ("ctx_2", "Country")),
            columns=(
                LookupColumn(slot="value_1", header="Height", kind="number", unit="m"),
                LookupColumn(slot="value_2", header="First ascent", kind="text"),
                LookupColumn(slot="value_3", header="Climbed in winter", kind="yes_no"),
            ),
        ).to_json()
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"question": "  "}, "The question must not be blank."),
        ({"question": "a" * 1001}, "at most 1000"),
        ({"question": "a\x00b"}, "control characters"),
        ({"context_columns": ()}, "Choose 1 to 30 columns that identify the row"),
        ({"context_columns": tuple(f"c{i}" for i in range(31))}, "Choose 1 to 30"),
        ({"context_columns": ("Mountain", "mountain")}, "'mountain' is chosen twice"),
        ({"context_columns": (" ",)}, "must not be blank"),
        ({"context_columns": ("a" * 101,)}, "at most 100 characters"),
        ({"context_columns": ("a\nb",)}, "control characters"),
        ({"targets": ()}, "Choose 1 to 20 columns to fill"),
        ({"targets": tuple(target(f"t{i}") for i in range(21))}, "Choose 1 to 20"),
        ({"targets": (target("Height"), target("height"))}, "'height' is to be filled twice"),
        ({"targets": (target("Country"),)}, "cannot both identify the row and be filled"),
        ({"targets": (target("COUNTRY"),)}, "cannot both identify the row and be filled"),
    ],
)
def test_a_request_is_refused_with_a_message(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        request(**changes)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"column": "H", "kind": "text", "unit": "m"}, "unit is only for a number column"),
        ({"column": "H", "kind": "yes_no", "unit": "m"}, "unit is only for a number column"),
        ({"column": "H", "kind": "number", "unit": " "}, "short plain text"),
        ({"column": "H", "kind": "number", "unit": "x" * 31}, "at most 30"),
        ({"column": "H", "kind": "date"}, "kind"),
        ({"column": " ", "kind": "text"}, "must not be blank"),
        ({"column": "H", "kind": "text", "extra": 1}, "extra"),
    ],
)
def test_a_target_is_refused_with_a_message(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        FallbackTarget(**kwargs)


def test_the_limits_are_those_of_a_lookup_task() -> None:
    biggest = request(
        context_columns=tuple(f"c{i}" for i in range(30)),
        targets=tuple(target(f"t{i}", "number", "m") for i in range(20)),
    )
    assert len(biggest.to_task().columns) == 20 and len(biggest.to_task().context) == 30


def test_the_request_is_frozen() -> None:
    with pytest.raises(ValidationError):
        request().question = "other"  # type: ignore[misc]


# --- The capability ------------------------------------------------------------------


def test_the_spec_is_typed_by_the_columns_and_the_kinds() -> None:
    task = request().to_task()
    spec = web_lookup_spec(task, {"ctx_1": DataType.TEXT, "ctx_2": DataType.NUMBER})
    assert spec.id == "web_lookup" and spec.parameters == {}
    assert {name: (item.type, item.required) for name, item in spec.inputs.items()} == {
        "task": (DataType.TEXT, True),
        "ctx_1": (DataType.TEXT, True),
        "ctx_2": (DataType.NUMBER, True),
    }
    assert spec.output.fields == {
        "value_1": DataType.NUMBER,
        "value_2": DataType.TEXT,
        "value_3": DataType.TEXT,
    }
    text = spec.description + spec.output.description
    assert "not verified" in text and "web search" in text


@pytest.mark.parametrize(
    "types",
    [
        {"ctx_1": DataType.TEXT},
        {"ctx_1": DataType.TEXT, "ctx_2": DataType.TEXT, "ctx_3": DataType.TEXT},
        {"ctx_1": DataType.TEXT, "ctx_2": DataType.QUANTITY},
    ],
)
def test_the_spec_needs_a_text_or_number_type_for_exactly_the_context_slots(
    types: dict[str, DataType],
) -> None:
    with pytest.raises(FallbackPlanError):
        web_lookup_spec(request().to_task(), types)


# --- The plan ------------------------------------------------------------------------


def test_the_plan_is_one_step_with_a_literal_task_and_bound_context() -> None:
    plan = build_fallback_plan(request(), COLUMNS)
    (step,) = plan.steps
    assert (step.id, step.capability, step.parameters) == ("lookup", "web_lookup", {})
    dumped = plan.model_dump(mode="json")
    inputs = dumped["steps"][0]["inputs"]
    assert inputs["task"] == {"kind": "literal", "value": request().to_task().to_json()}
    assert inputs["ctx_1"] == {"kind": "column", "name": "Mountain"}
    assert inputs["ctx_2"] == {"kind": "column", "name": "Country"}
    assert dumped["outputs"] == [
        {"step_id": "lookup", "value_field": "value_1", "target_column": "Height"},
        {"step_id": "lookup", "value_field": "value_2", "target_column": "First ascent"},
        {"step_id": "lookup", "value_field": "value_3", "target_column": "Climbed in winter"},
    ]
    assert "not verified" in plan.assumptions[0]
    assert is_fallback_plan(plan)


def test_the_same_request_gives_the_same_bytes() -> None:
    first = build_fallback_plan(request(), COLUMNS).model_dump_json()
    again = build_fallback_plan(request(), list(reversed(COLUMNS))).model_dump_json()
    assert first == again == build_fallback_plan(request(), COLUMNS).model_dump_json()
    assert build_fallback_plan(request(question="Other?"), COLUMNS).model_dump_json() != first


def test_the_application_validator_accepts_the_plan_and_the_intent_policy_ignores_it() -> None:
    plan = build_fallback_plan(request(), COLUMNS)
    validator = PlanValidator(
        fallback_registry(plan, COLUMNS),
        DatasetProfile(dataset_id="d", name="d", columns=COLUMNS),
        PlanValidationPolicy(
            max_steps=12, output_targets=frozenset({"Height", "First ascent", "Climbed in winter"})
        ),
    )
    # Instruction wording that makes the city policy fire for a geocode step does nothing here.
    for instruction in (
        None,
        "Find facts",
        "Use literal city names; do not require capital status.",
    ):
        assert validator.validate(plan, instruction=instruction).valid


def test_existing_and_untyped_targets_and_integer_columns_are_accepted() -> None:
    plan = build_fallback_plan(
        request(
            context_columns=("Mountain", "Rank"),
            targets=(
                target("Notes"),
                target("Score", "number"),
                target("Rank2", "text"),
                target("Odd", "yes_no"),
                target("Blank", "number"),
            ),
        ),
        COLUMNS,
    )
    registry = fallback_registry(plan, COLUMNS)
    assert registry.get("web_lookup").inputs["ctx_2"].type is DataType.NUMBER  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"context_columns": ("Mountain", "Nope")}, "The column 'Nope' does not exist"),
        (
            {"context_columns": ("Mountain", "country")},
            "does not exist in the table. Did you mean 'Country'?",
        ),
        ({"context_columns": ("Born",)}, "'Born' has Date values; only text and number"),
        ({"context_columns": ("Flag",)}, "'Flag' has Boolean values"),
        ({"context_columns": ("Odd",)}, "'Odd' has mixed values"),
        ({"context_columns": ("Blank",)}, "'Blank' has no values to tell its type from"),
        (
            {"targets": (target("Notes", "number"),)},
            "'Notes' holds Text values, but it is to be filled with number",
        ),
        (
            {"targets": (target("Score", "text"),)},
            "'Score' holds Number values, but it is to be filled with text",
        ),
        (
            {"targets": (target("Rank", "yes_no"),)},
            "'Rank' holds Integer values, but it is to be filled with yes/no",
        ),
        ({"targets": (target("Born"),)}, "'Born' has Date values and cannot be filled"),
        (
            {"targets": (target("Flag", "yes_no"),)},
            "'Flag' has Boolean values and cannot be filled",
        ),
        (
            {"targets": (target("notes"),)},
            "differs from an existing column only in letter case. Did you mean 'Notes'",
        ),
        ({"targets": (target("New "),)}, "must not start or end with a space"),
    ],
)
def test_a_plan_that_cannot_work_is_refused_with_a_message(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(FallbackPlanError, match=message):
        build_fallback_plan(request(**changes), COLUMNS)


def test_the_largest_request_fits_every_limit_even_with_characters_that_grow() -> None:
    quote = '"' * 100
    wide = "\U0001f600" * 100  # four bytes each in UTF-8
    context = tuple(f"{quote[:50]}{wide[:48]}{i:02d}" for i in range(30))
    targets = tuple(
        target(f"{quote[:40]}{wide[:58]}{i:02d}", "number", '"' * 30) for i in range(20)
    )
    big = FallbackRequest(question='"' * 1000, context_columns=context, targets=targets)
    columns = [ColumnProfile(name=name, inferred_type=T, null_count=0) for name in context]
    plan = build_fallback_plan(big, columns)
    (step,) = plan.steps
    literal = step.inputs["task"].value  # type: ignore[union-attr]
    assert isinstance(literal, str) and len(literal) < MAX_TASK_JSON
    assert len(plan.model_dump_json().encode()) < MAX_PLAN_BYTES
    assert len(plan.outputs) == 20 and len(step.inputs) == 31


# --- The registry --------------------------------------------------------------------


def test_the_registry_holds_the_offered_capabilities_and_the_one_of_this_plan() -> None:
    plan = build_fallback_plan(request(), COLUMNS)
    registry = fallback_registry(plan, COLUMNS)
    offered = [spec.id for spec in offered_capabilities(road_distance=False)]
    assert [spec.id for spec in registry.list_all()] == [*offered, "web_lookup"]
    assert "web_lookup" not in offered
    assert registry.get("web_lookup") == web_lookup_spec(
        request().to_task(), {"ctx_1": DataType.TEXT, "ctx_2": DataType.TEXT}
    )


def test_the_registry_can_start_from_the_registry_the_application_offers() -> None:
    offered = CapabilityRegistry()
    for spec in offered_capabilities(road_distance=True):
        offered.register(spec)
    registry = fallback_registry(build_fallback_plan(request(), COLUMNS), COLUMNS, offered)
    assert registry.get("road_distance") is not None and registry.get("web_lookup") is not None
    assert offered.get("web_lookup") is None  # the offered registry is not changed
    with pytest.raises(FallbackPlanError, match="already contain"):
        fallback_registry(build_fallback_plan(request(), COLUMNS), COLUMNS, registry)


def edited(mutate: Any) -> EnrichmentPlan:
    data = build_fallback_plan(request(), COLUMNS).model_dump(mode="json")
    mutate(data)
    return EnrichmentPlan.model_validate(data)


def drop_task(data: dict[str, Any]) -> None:
    del data["steps"][0]["inputs"]["task"]


def other_task(task: dict[str, Any]) -> Any:
    def mutate(data: dict[str, Any]) -> None:
        data["steps"][0]["inputs"]["task"]["value"] = json.dumps(task)

    return mutate


def set_in(path: tuple[Any, ...], value: Any) -> Any:
    def mutate(data: dict[str, Any]) -> None:
        node = data
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


STEP = ("steps", 0)
MALFORMED = [
    (lambda d: d["steps"].append({**d["steps"][0], "id": "second"}), "exactly one step"),
    (lambda d: d.update(steps=[]), "exactly one step"),
    (set_in((*STEP, "capability"), "geocode"), "uses 'web_lookup'"),
    (set_in((*STEP, "parameters"), {"required_role": "x"}), "takes no parameters"),
    (drop_task, "no lookup task as a text"),
    (set_in((*STEP, "inputs", "task"), {"kind": "column", "name": "Mountain"}), "no lookup task"),
    (set_in((*STEP, "inputs", "task"), {"kind": "literal", "value": 5}), "no lookup task"),
    (set_in((*STEP, "inputs", "task", "value"), "not json"), "cannot be read"),
    (
        set_in((*STEP, "inputs", "task", "value"), json.dumps(request().to_task().model_dump())),
        "canonical form",
    ),
    (
        set_in((*STEP, "inputs", "ctx_3"), {"kind": "column", "name": "Notes"}),
        "do not match the context",
    ),
    (lambda d: d["steps"][0]["inputs"].pop("ctx_2"), "do not match the context"),
    (
        set_in((*STEP, "inputs", "ctx_2"), {"kind": "literal", "value": "x"}),
        "must be the column 'Country'",
    ),
    (set_in((*STEP, "inputs", "ctx_2", "name"), "Notes"), "must be the column 'Country'"),
    (lambda d: d["outputs"].pop(), "outputs do not match"),
    (lambda d: d["outputs"].append(dict(d["outputs"][0])), "outputs do not match"),
    (set_in(("outputs", 1, "value_field"), "value_9"), "outputs do not match"),
    (set_in(("outputs", 1, "step_id"), "other"), "from the web lookup step"),
    (set_in(("outputs", 1, "target_column"), "Else"), "must be written to 'First ascent'"),
    (set_in(("outputs", 1, "expected_unit"), "m"), "no expected unit"),
]


@pytest.mark.parametrize(("mutate", "message"), MALFORMED)
def test_a_plan_that_is_not_a_fallback_plan_has_no_registry(mutate: Any, message: str) -> None:
    with pytest.raises(FallbackPlanError, match=message):
        fallback_registry(edited(mutate), COLUMNS)


def test_the_registry_needs_the_table_of_the_plan() -> None:
    plan = build_fallback_plan(request(), COLUMNS)
    with pytest.raises(FallbackPlanError, match="'Country' does not exist in the table"):
        fallback_registry(plan, [c for c in COLUMNS if c.name != "Country"])
    changed = [
        ColumnProfile(name=c.name, inferred_type=PrimitiveType.DATE, null_count=0)
        if c.name == "Country"
        else c
        for c in COLUMNS
    ]
    with pytest.raises(FallbackPlanError, match="only text and number columns"):
        fallback_registry(plan, changed)


def test_the_normal_validator_does_not_know_the_capability() -> None:
    plan = build_fallback_plan(request(), COLUMNS)
    plain = CapabilityRegistry()
    for spec in offered_capabilities(road_distance=False):
        plain.register(spec)
    result = PlanValidator(
        plain, DatasetProfile(dataset_id="d", name="d", columns=COLUMNS)
    ).validate(plan)
    assert [error.code for error in result.errors] == ["UNSUPPORTED_CAPABILITY"]


def test_is_fallback_plan_looks_at_the_capability_of_any_step() -> None:
    plan = build_fallback_plan(request(), COLUMNS)
    assert is_fallback_plan(plan)
    other = edited(set_in((*STEP, "capability"), "geocode"))
    assert not is_fallback_plan(other)
    both = edited(
        lambda d: d["steps"].append({**d["steps"][0], "id": "second", "capability": "geocode"})
    )
    assert is_fallback_plan(both)
