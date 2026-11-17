"""The mixed plan: the planner's plan plus one web lookup step, and the registry that knows it."""

import json
from pathlib import Path
from typing import Any

import pytest

from ai_data_enrichment.capabilities.definitions import offered_capabilities
from ai_data_enrichment.capabilities.fallback import (
    FallbackPlanError,
    FallbackRequest,
    FallbackTarget,
    build_fallback_plan,
    build_mixed_plan,
    fallback_registry,
    is_fallback_plan,
    plan_shape,
    web_lookup_spec,
)
from ai_data_enrichment.domain.capability import DataType
from ai_data_enrichment.domain.dataset_profile import ColumnProfile, DatasetProfile, PrimitiveType
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.validation.plan_validator import PlanValidationPolicy, PlanValidator

T, N = PrimitiveType.TEXT, PrimitiveType.NUMBER
COLUMNS = [
    ColumnProfile(name=name, inferred_type=kind, null_count=0)
    for name, kind in {
        "Mountain": T,
        "Country": T,
        "height": N,
        "Rank": N,
        "Notes": T,
    }.items()
]
PLANNER = EnrichmentPlan.model_validate(
    json.loads(Path("demo/mountains_api_plan.json").read_text("utf-8"))
)


def request(**changes: Any) -> FallbackRequest:
    base: dict[str, Any] = {
        "question": "Find the geological age of this mountain.",
        "context_columns": ("Mountain", "Country"),
        "targets": (FallbackTarget(column="age", kind="number", unit="million years"),),
    }
    return FallbackRequest(**{**base, **changes})


def mixed() -> EnrichmentPlan:
    return build_mixed_plan(PLANNER, request(), COLUMNS)


def edited(mutate: Any, plan: EnrichmentPlan | None = None) -> EnrichmentPlan:
    data = (plan or mixed()).model_dump(mode="json")
    mutate(data)
    return EnrichmentPlan.model_validate(data)


# --- build_mixed_plan -------------------------------------------------------------------


def test_the_planners_steps_and_outputs_are_kept_and_the_lookup_is_added_last() -> None:
    plan = mixed()
    own = PLANNER.model_dump(mode="json")
    combined = plan.model_dump(mode="json")
    assert combined["steps"][:-1] == own["steps"]
    assert combined["outputs"][: len(own["outputs"])] == own["outputs"]
    assert combined["version"] == own["version"]
    [lookup] = [step for step in plan.steps if step.capability == "web_lookup"]
    assert plan.steps[-1] is lookup and lookup.id == "lookup" and lookup.parameters == {}
    # One output per target of the request, written from the lookup step.
    added = plan.outputs[len(PLANNER.outputs) :]
    assert [(o.step_id, o.value_field, o.target_column) for o in added] == [
        ("lookup", "value_1", "age")
    ]
    assert combined["outputs"][0]["target_column"] == "height"
    # The step is the one build_fallback_plan builds for the same request.
    alone = build_fallback_plan(request(), COLUMNS)
    assert alone.steps[0].model_dump() == lookup.model_dump()
    assert plan.assumptions == [*PLANNER.assumptions, alone.assumptions[0]]
    assert plan_shape(plan) == "mixed" and is_fallback_plan(plan)


def test_the_same_inputs_give_the_same_bytes() -> None:
    assert mixed().model_dump_json() == mixed().model_dump_json()
    other = build_mixed_plan(PLANNER, request(question="Another question."), COLUMNS)
    assert other.model_dump_json() != mixed().model_dump_json()


def test_the_real_validator_accepts_it_with_every_requested_column() -> None:
    plan = mixed()
    profile = DatasetProfile(dataset_id="d", name="d", columns=COLUMNS)
    policy = PlanValidationPolicy(max_steps=12, output_targets=frozenset({"height", "age"}))
    result = PlanValidator(fallback_registry(plan, COLUMNS), profile, policy).validate(plan)
    assert result.valid, result.errors


@pytest.mark.parametrize(
    ("taken", "expected"),
    [(("lookup",), "lookup_2"), (("lookup", "lookup_2"), "lookup_3")],
)
def test_the_step_id_does_not_collide_with_a_step_of_the_planner(
    taken: tuple[str, ...], expected: str
) -> None:
    # Rename the planner's steps ("resolve", "elevation") to the ids that are taken.
    rename = dict(zip(("resolve", "elevation"), (*taken, "elevation")[:2], strict=False))
    data = json.loads(PLANNER.model_dump_json())
    for step in data["steps"]:
        for reference in step["inputs"].values():
            if reference.get("step_id") in rename:
                reference["step_id"] = rename[reference["step_id"]]
        step["id"] = rename.get(step["id"], step["id"])
    for output in data["outputs"]:
        output["step_id"] = rename.get(output["step_id"], output["step_id"])
    planner = EnrichmentPlan.model_validate(data)
    plan = build_mixed_plan(planner, request(), COLUMNS)
    assert plan.steps[-1].id == expected
    assert [o.step_id for o in plan.outputs][-1] == expected
    assert plan.model_dump_json() == build_mixed_plan(planner, request(), COLUMNS).model_dump_json()
    assert [s.id for s in plan.steps[:-1]] == [s.id for s in planner.steps]


def test_a_target_the_planner_already_writes_is_refused() -> None:
    with pytest.raises(FallbackPlanError, match="already fills the column 'height'"):
        build_mixed_plan(
            PLANNER,
            request(targets=(FallbackTarget(column="height", kind="number"),)),
            COLUMNS,
        )


def test_a_context_column_that_the_plan_writes_is_refused() -> None:
    with pytest.raises(FallbackPlanError, match="'height' identifies the row.*writes to it"):
        build_mixed_plan(PLANNER, request(context_columns=("Mountain", "height")), COLUMNS)
    # The other direction: a context column that the lookup itself would write is refused by
    # the request, before the plan is made.
    with pytest.raises(ValueError, match="cannot both identify the row"):
        request(context_columns=("Mountain", "age"))


def test_whatever_the_fallback_plan_refuses_is_refused() -> None:
    with pytest.raises(FallbackPlanError, match="'Cuntry' does not exist"):
        build_mixed_plan(PLANNER, request(context_columns=("Mountain", "Cuntry")), COLUMNS)
    with pytest.raises(FallbackPlanError, match="is to be filled with text"):
        build_mixed_plan(
            PLANNER, request(targets=(FallbackTarget(column="Rank", kind="text"),)), COLUMNS
        )
    with pytest.raises(FallbackPlanError, match="differs from an existing column only"):
        build_mixed_plan(
            PLANNER, request(targets=(FallbackTarget(column="NOTES", kind="text"),)), COLUMNS
        )


def test_a_plan_that_already_searches_the_web_is_not_extended() -> None:
    with pytest.raises(FallbackPlanError, match="must not search the web"):
        build_mixed_plan(
            mixed(), request(targets=(FallbackTarget(column="n", kind="text"),)), COLUMNS
        )


def test_a_plan_that_would_be_too_long_is_refused_by_the_validator() -> None:
    with pytest.raises(FallbackPlanError, match="not valid"):
        build_mixed_plan(PLANNER, request(), COLUMNS, max_steps=2)


# --- The shape and the registry ---------------------------------------------------------


def test_the_shape_follows_the_steps() -> None:
    assert plan_shape(PLANNER) == "planner"
    assert plan_shape(build_fallback_plan(request(), COLUMNS)) == "fallback"
    assert plan_shape(mixed()) == "mixed"


def test_the_registry_of_a_mixed_plan_holds_the_offered_capabilities_and_its_own_lookup() -> None:
    plan = mixed()
    registry = fallback_registry(plan, COLUMNS)
    offered = [spec.id for spec in offered_capabilities(road_distance=False)]
    assert [spec.id for spec in registry.list_all()] == [*offered, "web_lookup"]
    assert registry.get("web_lookup") == web_lookup_spec(
        request().to_task(), {"ctx_1": DataType.TEXT, "ctx_2": DataType.TEXT}
    )


def lookup_inputs(data: dict[str, Any]) -> dict[str, Any]:
    return data["steps"][-1]["inputs"]


REFUSED = [
    # Not one lookup step among the others.
    (lambda d: d["steps"].append({**d["steps"][-1], "id": "again"}), "exactly one step that uses"),
    (lambda d: d["steps"].pop(), "exactly one step that uses"),
    # A step the application does not offer.
    (lambda d: d["steps"][0].update(capability="no_such_capability"), "not offered"),
    # The lookup step.
    (lambda d: d["steps"][-1].update(parameters={"unit": "m"}), "takes no parameters"),
    (
        lambda d: lookup_inputs(d).update(ctx_1={"kind": "step_output", "step_id": "resolve"}),
        "must not use the result of another step",
    ),
    (lambda d: lookup_inputs(d).pop("task"), "no lookup task as a text"),
    (
        lambda d: lookup_inputs(d).update(ctx_2={"kind": "literal", "value": "x"}),
        "must be the column",
    ),
    # Another step uses the lookup's output.
    (
        lambda d: d["steps"][1]["inputs"].update(
            entity={"kind": "step_output", "step_id": "lookup"}
        ),
        "must not use the result of the web lookup step",
    ),
    # The outputs.
    (lambda d: d["outputs"].pop(), "outputs do not match"),
    (lambda d: d["outputs"][-1].update(value_field="value_9"), "outputs do not match"),
    (lambda d: d["outputs"][-1].update(expected_unit="m"), "no expected unit"),
    (lambda d: d["outputs"][-1].update(target_column="else"), "must be written to 'age'"),
    (lambda d: d["outputs"][0].update(target_column="Age"), "filled by the steps of the plan"),
    (lambda d: d["outputs"][0].update(target_column="Mountain"), "identifies the row"),
    (lambda d: d["outputs"][-1].update(target_column="Country"), "must be written to 'age'"),
]


@pytest.mark.parametrize(("mutate", "message"), REFUSED)
def test_a_plan_that_is_neither_shape_has_no_registry(mutate: Any, message: str) -> None:
    with pytest.raises(FallbackPlanError, match=message):
        fallback_registry(edited(mutate), COLUMNS)


def test_a_planner_plan_with_other_steps_but_no_lookup_has_no_registry() -> None:
    with pytest.raises(FallbackPlanError, match="exactly one step that uses 'web_lookup'"):
        fallback_registry(PLANNER, COLUMNS)


def test_a_context_column_written_by_any_output_is_refused_even_by_the_lookup() -> None:
    def write_context(data: dict[str, Any]) -> None:
        # The planner's own output now writes a context column of the lookup.
        data["outputs"][0]["target_column"] = "Country"

    with pytest.raises(FallbackPlanError, match="'Country' identifies the row.*writes to it"):
        fallback_registry(edited(write_context), COLUMNS)


def test_the_registry_needs_the_table_of_the_plan() -> None:
    with pytest.raises(FallbackPlanError, match="'Country' does not exist in the table"):
        fallback_registry(mixed(), [c for c in COLUMNS if c.name != "Country"])


def test_a_pure_fallback_plan_still_has_its_registry_and_no_other_steps() -> None:
    plan = build_fallback_plan(request(), COLUMNS)
    assert fallback_registry(plan, COLUMNS).get("web_lookup") is not None
    with pytest.raises(FallbackPlanError, match="uses 'web_lookup'"):
        fallback_registry(
            EnrichmentPlan.model_validate(
                {
                    **PLANNER.model_dump(mode="json"),
                    "steps": [PLANNER.model_dump(mode="json")["steps"][0]],
                }
            ),
            COLUMNS,
        )
