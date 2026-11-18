"""The extraction request, the one-step plan built from it and the registry that knows it."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from tests.support.mail_table import mail_table

from ai_data_enrichment.capabilities import extraction as extraction_module
from ai_data_enrichment.capabilities.definitions import offered_capabilities
from ai_data_enrichment.capabilities.extraction import (
    EXTRACTION_CAPABILITY,
    ExtractionPlanError,
    ExtractionRequest,
    ExtractionTarget,
    build_extraction_plan,
    extraction_registry,
    is_extraction_plan,
    text_extraction_spec,
)
from ai_data_enrichment.capabilities.fallback import (
    FallbackPlanError,
    FallbackRequest,
    FallbackTarget,
    build_fallback_plan,
    build_mixed_plan,
    is_fallback_plan,
    plan_shape,
)
from ai_data_enrichment.capabilities.plan_registry import registry_for_plan
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.capability import DataType
from ai_data_enrichment.domain.dataset_profile import ColumnProfile, DatasetProfile, PrimitiveType
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.plan_run import MAX_PLAN_BYTES
from ai_data_enrichment.providers import text_extraction as provider
from ai_data_enrichment.providers.text_extraction import MAX_TASK_JSON, ExtractionTask, parse_task
from ai_data_enrichment.providers.web_lookup import MAX_HEADER as WEB_MAX_HEADER
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy, PlanValidator

T, N = PrimitiveType.TEXT, PrimitiveType.NUMBER


@pytest.fixture(scope="module")
def columns(tmp_path_factory: pytest.TempPathFactory) -> list[ColumnProfile]:
    return mail_table(tmp_path_factory.mktemp("mail") / "mail.xlsx")


def request(**changes: Any) -> ExtractionRequest:
    base: dict[str, Any] = {
        "question": "Payments and subscriptions",
        "text_column": "Text",
        "targets": (
            ExtractionTarget(column="Service", kind="text"),
            ExtractionTarget(column="Amount", kind="number"),
            ExtractionTarget(column="Paid on", kind="date"),
            ExtractionTarget(column="Auto renews", kind="yes_no"),
            ExtractionTarget(column="Event", kind="one_of", options=("payment", "ended")),
        ),
    }
    return ExtractionRequest(**{**base, **changes})


def edited(plan: EnrichmentPlan, mutate: Any) -> EnrichmentPlan:
    data = plan.model_dump(mode="json")
    mutate(data)
    return EnrichmentPlan.model_validate(data)


# --- the request ------------------------------------------------------------------------


def test_the_limits_are_those_of_the_provider_and_of_the_other_search() -> None:
    assert provider.MAX_HEADER == WEB_MAX_HEADER  # the shared column-name check uses the latter


def test_a_request_is_strict_and_frozen() -> None:
    value = request()
    with pytest.raises(ValidationError):
        value.question = "x"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ExtractionRequest.model_validate(
            {"question": "q", "text_column": "Text", "targets": [], "other": 1}
        )
    with pytest.raises(ValidationError):
        ExtractionTarget.model_validate({"column": "A", "kind": "text", "unit": "m"})
    with pytest.raises(ValidationError):
        ExtractionTarget(column="A", kind="money")  # type: ignore[arg-type]


def test_the_question_is_trimmed_and_bounded() -> None:
    assert request(question="  Payments.  ").question == "Payments."
    for bad in ("", "   ", "a\nb", "a\tb", "a\x00b", "x" * 1001):
        with pytest.raises(ValidationError):
            request(question=bad)
    assert len(request(question="x" * 1000).question) == 1000


@pytest.mark.parametrize("bad", ["", "  ", "a\x00b", "a\nb", "x" * 101])
def test_names_of_columns_are_bounded_and_plain(bad: str) -> None:
    with pytest.raises(ValidationError):
        request(text_column=bad)
    with pytest.raises(ValidationError):
        request(targets=(ExtractionTarget(column=bad, kind="text"),))
    assert request(text_column="x" * 100).text_column == "x" * 100


def test_one_to_twenty_targets_each_filled_once() -> None:
    one = (ExtractionTarget(column="A", kind="text"),)
    assert len(request(targets=one).targets) == 1
    twenty = tuple(ExtractionTarget(column=f"C{i}", kind="text") for i in range(20))
    assert len(request(targets=twenty).targets) == 20
    for bad in ((), (*twenty, ExtractionTarget(column="C21", kind="text"))):
        with pytest.raises(ValidationError):
            request(targets=bad)
    for second in ("a", " A ", "A"):
        with pytest.raises(ValidationError, match="filled twice"):
            request(targets=(*one, ExtractionTarget(column=second, kind="number")))


def test_the_text_column_is_never_a_target() -> None:
    for name in ("Text", "text", " Text "):
        with pytest.raises(ValidationError, match="cannot both hold the text"):
            request(targets=(ExtractionTarget(column=name, kind="text"),))


def test_a_choice_needs_two_to_twenty_different_plain_options_and_nothing_else_has_any() -> None:
    def one_of(*options: str) -> ExtractionTarget:
        return ExtractionTarget(column="Event", kind="one_of", options=options)

    assert one_of("a", "b").options == ("a", "b")
    assert len(one_of(*(f"o{i}" for i in range(20))).options) == 20
    assert len(one_of("a", "x" * 60).options[1]) == 60
    for bad in (
        (),
        ("a",),
        tuple(f"o{i}" for i in range(21)),
        ("a", "A"),
        ("a", " a "),
        ("a", ""),
        ("a", "  "),
        ("a", "b\nc"),
        ("a", "x" * 61),
    ):
        with pytest.raises(ValidationError):
            one_of(*bad)
    for kind in ("text", "number", "date", "yes_no"):
        with pytest.raises(ValidationError, match="Options are only for a choice"):
            ExtractionTarget(column="A", kind=kind, options=("a", "b"))  # type: ignore[arg-type]


def test_the_task_has_slots_in_the_order_of_the_request() -> None:
    task = request(
        targets=(
            ExtractionTarget(column=" Amount ", kind="number"),
            ExtractionTarget(column="Event", kind="one_of", options=(" payment", "ended ")),
        )
    ).to_task()
    assert [(slot, c.header, c.kind, c.options) for slot, c in task.slots] == [
        ("value_1", "Amount", "number", ()),
        ("value_2", "Event", "one_of", ("payment", "ended")),
    ]
    assert task.question == "Payments and subscriptions"


# --- the task as a text -------------------------------------------------------------------


def test_a_task_text_round_trips_and_is_compact_and_the_same_every_time() -> None:
    task = request().to_task()
    text = task.to_json()
    assert parse_task(text) == task and parse_task(text).to_json() == text  # type: ignore[union-attr]
    assert text == request().to_task().to_json()
    assert json.loads(text)["columns"][4] == {
        "header": "Event",
        "kind": "one_of",
        "options": ["payment", "ended"],
    }
    assert " " not in text.replace("Payments and subscriptions", "").replace("Paid on", "").replace(
        "Auto renews", ""
    )


@pytest.mark.parametrize(
    "text",
    [
        None,
        1,
        b"{}",
        "",
        "not json",
        "[]",
        "{}",
        '{"question":"q"}',
        '{"question":"q","columns":[]}',
        '{"question":"q","columns":[{"header":"A","kind":"money"}]}',
        '{"question":"q","columns":[{"header":"A","kind":"text","x":1}]}',
        '{"question":"q","columns":[{"header":"A"}]}',
        '{"question":"q","columns":[{"header":"A","kind":"one_of"}]}',
        '{"question":"q","columns":[{"header":"A","kind":"one_of","options":["a"]}]}',
        '{"question":"q","columns":[{"header":"A","kind":"text","options":["a","b"]}]}',
        '{"question":"q","columns":[{"header":"A","kind":"text","options":[]}]}',
        '{"question":"q","columns":[{"header":1,"kind":"text"}]}',
        '{"question":"q","columns":[{"header":"A","kind":"one_of","options":["a",2]}]}',
        '{"question":"q","columns":[{"header":"A","kind":"one_of","options":"ab"}]}',
        '{"question":1,"columns":[{"header":"A","kind":"text"}]}',
        '{"question":"q","columns":"A"}',
        '{"question":"q","columns":["A"]}',
        '{"question":"q","columns":[{"header":"A","kind":"text"},{"header":"a","kind":"text"}]}',
        '{"question":" q","columns":[{"header":"A","kind":"text"}]}',
        '{"question":"q","columns":[{"header":"A ","kind":"text"}]}',
        '{"question":"q", "columns":[{"header":"A","kind":"text"}]}',
        '{"columns":[{"header":"A","kind":"text"}],"question":"q"}',
        '{"question":"q","question":"r","columns":[{"header":"A","kind":"text"}]}',
        '{"question":"q\\n","columns":[{"header":"A","kind":"text"}]}',
        "[" * 100_000,
        "x" * (MAX_TASK_JSON + 1),
    ],
)
def test_any_other_text_is_no_task_and_never_an_exception(text: Any) -> None:
    assert parse_task(text) is None


def test_the_canonical_small_task_is_read() -> None:
    assert parse_task('{"question":"q","columns":[{"header":"A","kind":"text"}]}') is not None


# --- the capability for one plan ----------------------------------------------------------


def test_the_spec_has_the_task_and_the_text_and_one_typed_field_per_slot() -> None:
    task = request().to_task()
    spec = text_extraction_spec(task)
    assert spec.id == EXTRACTION_CAPABILITY == "text_extraction"
    assert {name: item.type for name, item in spec.inputs.items()} == {
        "task": DataType.TEXT,
        "text": DataType.TEXT,
    }
    assert all(item.required for item in spec.inputs.values()) and spec.parameters == {}
    # A number is a number; a date is text (YYYY-MM-DD), as are yes/no and a choice.
    assert spec.output.fields == {
        "value_1": DataType.TEXT,
        "value_2": DataType.NUMBER,
        "value_3": DataType.TEXT,
        "value_4": DataType.TEXT,
        "value_5": DataType.TEXT,
    }
    assert spec.output.type is DataType.TEXT
    # Derived from the task, so another task is another contract.
    other = ExtractionTask("q", (provider.ExtractionColumn("A", "number"),))
    assert text_extraction_spec(other).output.fields == {"value_1": DataType.NUMBER}


# --- the plan -----------------------------------------------------------------------------


def test_the_plan_for_the_mail_table_is_one_step_and_the_validator_accepts_it(
    columns: list[ColumnProfile],
) -> None:
    value = request()
    plan = build_extraction_plan(value, columns)
    assert [name for name in (c.name for c in columns)] == ["Received", "From", "Subject", "Text"]
    [step] = plan.steps
    assert step.id == "extract" and step.capability == "text_extraction" and step.parameters == {}
    assert step.inputs["task"].model_dump() == {  # type: ignore[union-attr]
        "kind": "literal",
        "value": value.to_task().to_json(),
    }
    assert step.inputs["text"].model_dump() == {"kind": "column", "name": "Text"}  # type: ignore[union-attr]
    assert [(o.step_id, o.value_field, o.target_column, o.expected_unit) for o in plan.outputs] == [
        ("extract", f"value_{i}", t.column, None) for i, t in enumerate(value.targets, start=1)
    ]
    assert (
        len(plan.assumptions) == 1
        and "kept only where the passage it quotes" in (plan.assumptions[0])
    )
    # The same bytes every time; another request, other bytes.
    assert plan.model_dump_json() == build_extraction_plan(request(), columns).model_dump_json()
    assert (
        plan.model_dump_json()
        != build_extraction_plan(request(question="Other"), columns).model_dump_json()
    )

    # The application's own validator, unchanged, with the registry made for the plan.
    result = PlanValidator(
        extraction_registry(plan, columns),
        DatasetProfile(dataset_id="d", name="d", columns=columns),
        PlanValidationPolicy(
            max_steps=12, output_targets=frozenset(t.column for t in value.targets)
        ),
    ).validate(plan, instruction=value.question)
    assert result.valid, result.errors


def test_a_plan_has_the_shape_of_an_extraction_plan_and_no_other(
    columns: list[ColumnProfile],
) -> None:
    plan = build_extraction_plan(request(), columns)
    assert plan_shape(plan) == "extraction" and is_extraction_plan(plan)
    assert not is_fallback_plan(plan)


def test_existing_columns_may_be_filled_when_their_type_fits() -> None:
    table = [
        ColumnProfile(name=name, inferred_type=kind, null_count=0)
        for name, kind in {
            "Body": T,
            "Service": T,
            "Amount": N,
            "Count": PrimitiveType.INTEGER,
            "Odd": PrimitiveType.MIXED,
            "Blank": PrimitiveType.UNKNOWN,
        }.items()
    ]
    targets = (
        ExtractionTarget(column="Service", kind="date"),
        ExtractionTarget(column="Amount", kind="number"),
        ExtractionTarget(column="Count", kind="number"),
        ExtractionTarget(column="Odd", kind="yes_no"),
        ExtractionTarget(column="Blank", kind="one_of", options=("a", "b")),
        ExtractionTarget(column="New one", kind="text"),
    )
    plan = build_extraction_plan(request(text_column="Body", targets=targets), table)
    assert [o.target_column for o in plan.outputs] == [t.column for t in targets]


def test_the_table_decides_what_a_request_may_ask(columns: list[ColumnProfile]) -> None:
    def refused(match: str, **changes: Any) -> None:
        with pytest.raises(ExtractionPlanError, match=match):
            build_extraction_plan(request(**changes), columns)

    refused("does not exist in the table. Did you mean 'Text'", text_column="text")
    refused("does not exist in the table", text_column="Body")
    # Not a text column: a date column, and text columns are the only ones read.
    refused(
        "'Received' has DateTime values; only a text column can be read", text_column="Received"
    )
    one = (ExtractionTarget(column="Total", kind="number"),)
    # Existing columns follow the rules of the other search for what they may be filled with.
    refused(
        "'Subject' holds Text values, but it is to be filled with number values",
        targets=(ExtractionTarget(column="Subject", kind="number"),),
    )
    refused(
        "differs from an existing column only in letter case. Did you mean 'Subject'",
        targets=(ExtractionTarget(column="subject", kind="text"),),
    )
    refused(
        "must not start or end with a space",
        targets=(ExtractionTarget(column=" Total", kind="number"),),
    )
    assert build_extraction_plan(request(targets=one), columns).outputs[0].target_column == "Total"


def test_a_target_that_is_the_text_column_is_refused_by_the_plan_as_well(
    columns: list[ColumnProfile],
) -> None:
    sneaky = ExtractionRequest.model_construct(
        question="q",
        text_column="Text",
        targets=(ExtractionTarget(column="Text", kind="text"),),
    )
    with pytest.raises(ExtractionPlanError, match="cannot both hold the text and be filled"):
        build_extraction_plan(sneaky, columns)


def test_columns_that_cannot_be_filled_are_refused() -> None:
    table = [
        ColumnProfile(name=name, inferred_type=kind, null_count=0)
        for name, kind in {
            "Body": T,
            "Day": PrimitiveType.DATE,
            "Flag": PrimitiveType.BOOLEAN,
        }.items()
    ]
    for name in ("Day", "Flag"):
        with pytest.raises(ExtractionPlanError, match="and cannot be filled"):
            build_extraction_plan(
                request(text_column="Body", targets=(ExtractionTarget(column=name, kind="text"),)),
                table,
            )


def largest(letter: str) -> ExtractionRequest:
    """20 targets with 20 options each, every name as long as it may be."""
    targets = tuple(
        ExtractionTarget(
            column=f"{i:02d}" + letter * 98,
            kind="one_of",
            options=tuple(f"{j:02d}" + letter * 58 for j in range(20)),
        )
        for i in range(20)
    )
    return request(text_column="Body", question=letter * 1000, targets=targets)


BODY = [ColumnProfile(name="Body", inferred_type=T, null_count=0)]


def test_the_largest_request_fits_the_task_and_the_plan() -> None:
    plan = build_extraction_plan(largest("a"), BODY)
    task = plan.steps[0].inputs["task"].value  # type: ignore[union-attr]
    assert isinstance(task, str)
    assert MAX_TASK_JSON - 2000 < len(task) <= MAX_TASK_JSON
    assert len(plan.model_dump_json().encode()) <= MAX_PLAN_BYTES
    assert extraction_module.MAX_PLAN_BYTES == MAX_PLAN_BYTES


def test_a_request_whose_plan_is_over_64_kib_in_bytes_is_refused_with_a_sentence() -> None:
    # Four-byte characters: the task is below its limit in characters, the plan above its.
    with pytest.raises(ExtractionPlanError, match="plan is too long. Use fewer or shorter"):
        build_extraction_plan(largest("𝔸"), BODY)


def test_a_task_text_over_the_limit_is_refused_with_a_sentence() -> None:
    quotes = tuple(f"{j:02d}" + '"' * 58 for j in range(20))  # every quote is escaped
    targets = tuple(
        ExtractionTarget(column=f"C{i}", kind="one_of", options=quotes) for i in range(20)
    )
    with pytest.raises(ExtractionPlanError, match="task is too long. Use fewer or shorter"):
        build_extraction_plan(request(text_column="Body", targets=targets), BODY)


# --- the registry for one plan ------------------------------------------------------------


def test_the_registry_holds_the_spec_of_the_plan_and_nothing_else(
    columns: list[ColumnProfile],
) -> None:
    plan = build_extraction_plan(request(), columns)
    registry = extraction_registry(plan, columns)
    assert [spec.id for spec in registry.list_all()] == ["text_extraction"]
    assert registry.list_all()[0] == text_extraction_spec(request().to_task())
    for offered in offered_capabilities(road_distance=True):
        assert registry.get(offered.id) is None


def malformed(columns: list[ColumnProfile]) -> dict[str, EnrichmentPlan]:
    plan = build_extraction_plan(request(), columns)
    lookup = build_fallback_plan(
        FallbackRequest(
            question="q",
            context_columns=("Subject",),
            targets=(FallbackTarget(column="Height", kind="number"),),
        ),
        columns,
    )
    planner_step = {
        "id": "dist",
        "capability": "geodesic_distance",
        "inputs": {"lat1": {"kind": "literal", "value": 1.0}},
        "parameters": {},
    }

    def mutate(function: Any) -> EnrichmentPlan:
        return edited(plan, function)

    def second(data: dict[str, Any]) -> None:
        data["steps"].append({**data["steps"][0], "id": "extract_2"})

    def with_step(step: dict[str, Any]) -> Any:
        return lambda data: data["steps"].append(step)

    return {
        "no step": mutate(lambda d: d.update(steps=[], outputs=[])),
        "a second extraction step": mutate(second),
        "a web lookup step next to it": EnrichmentPlan.model_validate(
            {
                **plan.model_dump(mode="json"),
                "steps": [
                    *plan.model_dump(mode="json")["steps"],
                    *lookup.model_dump(mode="json")["steps"],
                ],
            }
        ),
        "a planner step next to it": mutate(with_step(planner_step)),
        "the web lookup alone": lookup,
        "a parameter": mutate(lambda d: d["steps"][0].update(parameters={"a": "b"})),
        "a task that is a column": mutate(
            lambda d: d["steps"][0]["inputs"].update(task={"kind": "column", "name": "Text"})
        ),
        "a task that is a number": mutate(
            lambda d: d["steps"][0]["inputs"].update(task={"kind": "literal", "value": 1.0})
        ),
        "a task that is garbage": mutate(
            lambda d: d["steps"][0]["inputs"].update(task={"kind": "literal", "value": "{"})
        ),
        "a task with other spacing": mutate(
            lambda d: d["steps"][0]["inputs"]["task"].update(
                value=json.dumps(json.loads(d["steps"][0]["inputs"]["task"]["value"]), indent=1)
            )
        ),
        "no text input": mutate(lambda d: d["steps"][0]["inputs"].pop("text")),
        "an input too many": mutate(
            lambda d: d["steps"][0]["inputs"].update(more={"kind": "column", "name": "From"})
        ),
        "a text that is a literal": mutate(
            lambda d: d["steps"][0]["inputs"].update(text={"kind": "literal", "value": "x"})
        ),
        "a text that is a step": mutate(
            lambda d: d["steps"][0]["inputs"].update(
                text={"kind": "step_output", "step_id": "extract"}
            )
        ),
        "an output missing": mutate(lambda d: d["outputs"].pop()),
        "an output too many": mutate(
            lambda d: d["outputs"].append({**d["outputs"][0], "target_column": "Other"})
        ),
        "an output from another step": mutate(lambda d: d["outputs"][0].update(step_id="other")),
        "an output to another column": mutate(
            lambda d: d["outputs"][0].update(target_column="Other")
        ),
        "an output with a unit": mutate(lambda d: d["outputs"][0].update(expected_unit="m")),
        "an output that writes the text": mutate(
            lambda d: d["outputs"][0].update(target_column=" text")
        ),
    }


CASES = [
    "no step",
    "a second extraction step",
    "a web lookup step next to it",
    "a planner step next to it",
    "the web lookup alone",
    "a parameter",
    "a task that is a column",
    "a task that is a number",
    "a task that is garbage",
    "a task with other spacing",
    "no text input",
    "an input too many",
    "a text that is a literal",
    "a text that is a step",
    "an output missing",
    "an output too many",
    "an output from another step",
    "an output to another column",
    "an output with a unit",
    "an output that writes the text",
]


@pytest.mark.parametrize("case", CASES)
def test_every_malformed_shape_is_refused_with_the_dedicated_error(
    columns: list[ColumnProfile], case: str
) -> None:
    plan = malformed(columns)[case]
    with pytest.raises(ExtractionPlanError):
        extraction_registry(plan, columns)


def test_the_table_must_hold_the_text_column_and_it_must_be_text(
    columns: list[ColumnProfile],
) -> None:
    plan = build_extraction_plan(request(), columns)
    with pytest.raises(ExtractionPlanError, match="does not exist in the table"):
        extraction_registry(plan, [c for c in columns if c.name != "Text"])
    changed = [
        c.model_copy(update={"inferred_type": N}) if c.name == "Text" else c for c in columns
    ]
    with pytest.raises(ExtractionPlanError, match="only a text column can be read"):
        extraction_registry(plan, changed)


def test_the_error_is_its_own_and_not_a_fallback_error() -> None:
    assert issubclass(ExtractionPlanError, ValueError)
    assert not issubclass(ExtractionPlanError, FallbackPlanError)


# --- plan_shape and the dispatcher --------------------------------------------------------

PLANNER = EnrichmentPlan.model_validate(
    json.loads(Path("demo/mountains_api_plan.json").read_text("utf-8"))
)
MOUNTAINS = [
    ColumnProfile(name=name, inferred_type=kind, null_count=0)
    for name, kind in {"Mountain": T, "Country": T, "height": N, "Rank": N, "Notes": T}.items()
]


def offered() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for spec in offered_capabilities(road_distance=False):
        registry.register(spec)
    return registry


def test_every_shape_is_told_by_the_steps_alone(columns: list[ColumnProfile]) -> None:
    fallback = build_fallback_plan(
        FallbackRequest(
            question="q",
            context_columns=("Mountain",),
            targets=(FallbackTarget(column="age", kind="number"),),
        ),
        MOUNTAINS,
    )
    mixed = build_mixed_plan(
        PLANNER,
        FallbackRequest(
            question="q",
            context_columns=("Mountain", "Country"),
            targets=(FallbackTarget(column="age", kind="number"),),
        ),
        MOUNTAINS,
    )
    extraction = build_extraction_plan(request(), columns)
    assert [plan_shape(p) for p in (PLANNER, fallback, mixed, extraction)] == [
        "planner",
        "fallback",
        "mixed",
        "extraction",
    ]
    # Any step of this capability makes the shape: it is never taken for another one.
    both = EnrichmentPlan.model_validate(
        {
            **extraction.model_dump(mode="json"),
            "steps": [
                *extraction.model_dump(mode="json")["steps"],
                *fallback.model_dump(mode="json")["steps"],
            ],
        }
    )
    assert plan_shape(both) == "extraction" and is_extraction_plan(both)
    with_planner = EnrichmentPlan.model_validate(
        {
            **PLANNER.model_dump(mode="json"),
            "steps": [
                *PLANNER.model_dump(mode="json")["steps"],
                *extraction.model_dump(mode="json")["steps"],
            ],
        }
    )
    assert plan_shape(with_planner) == "extraction"
    assert not is_extraction_plan(PLANNER) and not is_extraction_plan(fallback)


def test_a_plan_that_reads_texts_cannot_be_completed_with_the_web_lookup(
    columns: list[ColumnProfile],
) -> None:
    extraction = build_extraction_plan(request(), columns)
    with pytest.raises(FallbackPlanError, match="must not read texts"):
        build_mixed_plan(
            extraction,
            FallbackRequest(
                question="q",
                context_columns=("Subject",),
                targets=(FallbackTarget(column="age", kind="number"),),
            ),
            columns,
        )


def test_the_dispatcher_gives_each_shape_its_own_registry(columns: list[ColumnProfile]) -> None:
    registry = offered()
    # The planner's plan is run with what is offered, the very object.
    assert registry_for_plan(PLANNER, MOUNTAINS, registry) is registry

    fallback = build_fallback_plan(
        FallbackRequest(
            question="q",
            context_columns=("Mountain",),
            targets=(FallbackTarget(column="age", kind="number"),),
        ),
        MOUNTAINS,
    )
    mixed = build_mixed_plan(
        PLANNER,
        FallbackRequest(
            question="q",
            context_columns=("Mountain", "Country"),
            targets=(FallbackTarget(column="age", kind="number"),),
        ),
        MOUNTAINS,
    )
    ids = {spec.id for spec in registry.list_all()}
    for plan in (fallback, mixed):
        found = registry_for_plan(plan, MOUNTAINS, registry)
        assert found is not registry
        assert {spec.id for spec in found.list_all()} == {*ids, "web_lookup"}
    assert registry.get("web_lookup") is None  # what is offered is never changed

    extraction = build_extraction_plan(request(), columns)
    found = registry_for_plan(extraction, columns, registry)
    assert [spec.id for spec in found.list_all()] == ["text_extraction"]


def test_the_dispatcher_refuses_a_plan_that_is_not_well_formed_for_its_shape(
    columns: list[ColumnProfile],
) -> None:
    extraction = build_extraction_plan(request(), columns)
    for plan in (
        malformed(columns)["a web lookup step next to it"],
        malformed(columns)["no text input"],
    ):
        with pytest.raises(ExtractionPlanError):
            registry_for_plan(plan, columns, offered())
    broken = edited(
        build_fallback_plan(
            FallbackRequest(
                question="q",
                context_columns=("Mountain",),
                targets=(FallbackTarget(column="age", kind="number"),),
            ),
            MOUNTAINS,
        ),
        lambda d: d["steps"][0].update(parameters={"a": "b"}),
    )
    with pytest.raises(FallbackPlanError):
        registry_for_plan(broken, MOUNTAINS, offered())
    assert extraction  # built without an error above
