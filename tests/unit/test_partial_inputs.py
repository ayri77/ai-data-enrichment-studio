"""Steps of a capability that accepts partial inputs run on the values a row has."""

from collections.abc import Mapping

from tests.support.web_lookup import FakeApi

from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    LiteralValue,
    OutputMapping,
    PlanStep,
)
from ai_data_enrichment.execution.executor import DAGExecutor
from ai_data_enrichment.execution.models import OperationResult, RuntimeValue
from ai_data_enrichment.providers.web_lookup import LookupColumn, LookupTask, WebLookupUsage
from ai_data_enrichment.providers.web_lookup_handler import WebLookupHandler


class Recorder:
    def __init__(self) -> None:
        self.inputs: list[dict[str, RuntimeValue]] = []

    def execute(
        self, *, inputs: Mapping[str, RuntimeValue], parameters: Mapping[str, str]
    ) -> OperationResult:
        self.inputs.append(dict(inputs))
        return OperationResult(status="success", value="ok")


def plan_for(capability: str) -> EnrichmentPlan:
    return EnrichmentPlan(
        version="1",
        steps=[
            PlanStep(
                id="step",
                capability=capability,
                inputs={
                    "task": LiteralValue(kind="literal", value="t"),
                    "ctx_1": ColumnReference(kind="column", name="A"),
                    "ctx_2": ColumnReference(kind="column", name="B"),
                },
                parameters={},
            )
        ],
        outputs=[OutputMapping(step_id="step", value_field="value", target_column="Out")],
        assumptions=[],
    )


def test_an_empty_cell_still_fails_the_step_by_default() -> None:
    handler = Recorder()
    row = {"A": "x", "B": None}
    result = DAGExecutor({"cap": handler}).execute_row(plan_for("cap"), row)
    (step,) = result.steps
    assert step.result.error_code == "MISSING_VALUE"
    assert step.result.message == "Empty input value(s): B."
    assert handler.inputs == []


def test_an_empty_cell_fails_a_step_of_another_capability_even_with_the_option() -> None:
    handler = Recorder()
    executor = DAGExecutor({"cap": handler}, partial_input_capabilities={"other"})
    (step,) = executor.execute_row(plan_for("cap"), {"A": "x", "B": "  "}).steps
    assert step.result.error_code == "MISSING_VALUE"
    assert handler.inputs == []


def test_a_partial_step_gets_only_the_inputs_that_have_a_value() -> None:
    handler = Recorder()
    executor = DAGExecutor({"cap": handler}, partial_input_capabilities={"cap"})
    (step,) = executor.execute_row(plan_for("cap"), {"A": "x", "B": " "}).steps
    assert step.result.status == "success"
    assert handler.inputs == [{"task": "t", "ctx_1": "x"}]


def test_a_partial_step_with_all_cells_filled_gets_every_input() -> None:
    handler = Recorder()
    executor = DAGExecutor({"cap": handler}, partial_input_capabilities={"cap"})
    executor.execute_row(plan_for("cap"), {"A": "x", "B": 2.0})
    assert handler.inputs == [{"task": "t", "ctx_1": "x", "ctx_2": 2.0}]


def test_a_missing_column_still_fails_a_partial_step() -> None:
    handler = Recorder()
    executor = DAGExecutor({"cap": handler}, partial_input_capabilities={"cap"})
    (step,) = executor.execute_row(plan_for("cap"), {"A": "x"}).steps
    assert step.result.error_code == "MISSING_COLUMN"
    assert handler.inputs == []


def lookup_plan() -> EnrichmentPlan:
    task = LookupTask(
        question="q",
        context=(("ctx_1", "A"), ("ctx_2", "B")),
        columns=(LookupColumn(slot="value_1", header="Out", kind="text"),),
    )
    plan = plan_for("web_lookup")
    plan.steps[0].inputs["task"] = LiteralValue(kind="literal", value=task.to_json())
    return plan


def handler(api: FakeApi) -> WebLookupHandler:
    return WebLookupHandler(api, model="m", reasoning_effort=None, usage=WebLookupUsage())


def test_web_lookup_with_one_empty_cell_reaches_the_handler_without_it() -> None:
    api = FakeApi(RuntimeError("stop here"))
    executor = DAGExecutor({"web_lookup": handler(api)}, partial_input_capabilities={"web_lookup"})
    (step,) = executor.execute_row(lookup_plan(), {"A": "Everest", "B": None}).steps
    assert step.result.error_code == "PROVIDER_ERROR"  # the handler ran and made its request
    assert len(api.calls) == 1
    sent = api.calls[0]["input"]
    assert "- A: Everest" in sent and "B:" not in sent


def test_web_lookup_with_every_cell_empty_makes_no_request() -> None:
    api = FakeApi(RuntimeError("must not be called"))
    executor = DAGExecutor({"web_lookup": handler(api)}, partial_input_capabilities={"web_lookup"})
    (step,) = executor.execute_row(lookup_plan(), {"A": None, "B": "  "}).steps
    assert step.result.error_code == "MISSING_VALUE"
    assert step.result.message == "The row has no value to search for."
    assert api.calls == []
