"""The universal-search fallback: a plan the server builds, never the planner.

After the planner has refused, the user may start a plan that has one step, the
capability ``web_lookup``: for each row a model with web search answers several target
columns. Nothing here calls a model or a service. This module holds

* ``FallbackRequest``: what the user chose (question, context columns, targets);
* ``build_fallback_plan``: the plan that follows from it, the same bytes every time;
* ``web_lookup_spec`` and ``fallback_registry``: the capability as it holds for ONE plan.

The capability is not part of ``offered_capabilities`` and not of ``BINDINGS``: the
planner is never told about it. Its contract depends on the plan (one input per context
column, one output field per target), so it is derived from the plan's own task text.
"""

import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from ai_data_enrichment.capabilities.definitions import offered_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.capability import (
    CapabilitySpec,
    DataType,
    InputSpec,
    OutputSpec,
)
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    LiteralValue,
    OutputMapping,
    PlanStep,
)
from ai_data_enrichment.providers.web_lookup import (
    LOOKUP_CAPABILITY,
    MAX_COLUMNS,
    MAX_CONTEXT,
    MAX_HEADER,
    MAX_QUESTION,
    MAX_UNIT,
    LookupColumn,
    LookupTask,
)
from ai_data_enrichment.validation.plan_validator import (
    _PRIMITIVE_TYPE_MAP,  # pyright: ignore[reportPrivateUsage]
    PlanValidationPolicy,
    PlanValidator,
)

STEP_ID = "lookup"
PLAN_VERSION = "1.0"
SPEC_VERSION = "1.0"
ASSUMPTION = (
    "Each value is found by an AI model with web search, one request per row. "
    "The values are not verified; each cell says which pages it rests on."
)

TargetKind = Literal["text", "number", "yes_no"]
_FIELD_TYPES: Mapping[str, DataType] = {
    "text": DataType.TEXT,
    "yes_no": DataType.TEXT,
    "number": DataType.NUMBER,
}
_KIND_WORDS: Mapping[str, str] = {"text": "text", "number": "number", "yes_no": "yes/no"}


class FallbackPlanError(ValueError):
    """A request or a plan the fallback cannot accept; the message is meant for a user."""


def _has_control(text: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in text)


def _column_name(value: str, what: str) -> str:
    if not value.strip():
        raise ValueError(f"A {what} column name must not be blank.")
    if len(value.strip()) > MAX_HEADER:
        raise ValueError(f"A {what} column name may have at most {MAX_HEADER} characters.")
    if _has_control(value):
        raise ValueError(f"A {what} column name must not contain control characters.")
    return value


class FallbackTarget(BaseModel):
    """One column to fill: an existing header or the name of a new column."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    column: str
    kind: TargetKind
    unit: str | None = None

    @field_validator("column")
    @classmethod
    def _column(cls, value: str) -> str:
        return _column_name(value, "target")

    @field_validator("unit")
    @classmethod
    def _unit(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or _has_control(value):
            raise ValueError("A unit must be a short plain text.")
        if len(value.strip()) > MAX_UNIT:
            raise ValueError(f"A unit may have at most {MAX_UNIT} characters.")
        return value

    @model_validator(mode="after")
    def _unit_only_for_numbers(self) -> "FallbackTarget":
        if self.unit is not None and self.kind != "number":
            raise ValueError(
                f"A unit is only for a number column; '{self.column}' is {_KIND_WORDS[self.kind]}."
            )
        return self


class FallbackRequest(BaseModel):
    """What the user asks the web search to find, for every row.

    At most 30 context columns and 20 targets, the limits of a lookup task. The largest
    request (every name 100 characters of quotes and four-byte characters) gives a task
    text of about 12,000 characters, below the 30,000 a task may have, and a plan of about
    46 KB, below the 64 KiB a plan may have (tests/unit/test_fallback_plan.py).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    context_columns: tuple[str, ...]
    targets: tuple[FallbackTarget, ...]

    @field_validator("question")
    @classmethod
    def _question(cls, value: str) -> str:
        if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in value):
            raise ValueError("The question must not contain control characters.")
        if not value.strip():
            raise ValueError("The question must not be blank.")
        if len(value.strip()) > MAX_QUESTION:
            raise ValueError(f"The question may have at most {MAX_QUESTION} characters.")
        return value.strip()

    @field_validator("context_columns")
    @classmethod
    def _context(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(value) <= MAX_CONTEXT:
            raise ValueError(f"Choose 1 to {MAX_CONTEXT} columns that identify the row.")
        for name in value:
            _column_name(name, "context")
        seen: set[str] = set()
        for name in value:
            if name.casefold() in seen:
                raise ValueError(f"The column '{name}' is chosen twice.")
            seen.add(name.casefold())
        return value

    @field_validator("targets")
    @classmethod
    def _targets(cls, value: tuple[FallbackTarget, ...]) -> tuple[FallbackTarget, ...]:
        if not 1 <= len(value) <= MAX_COLUMNS:
            raise ValueError(f"Choose 1 to {MAX_COLUMNS} columns to fill.")
        seen: set[str] = set()
        for target in value:
            if target.column.casefold() in seen:
                raise ValueError(f"The column '{target.column}' is to be filled twice.")
            seen.add(target.column.casefold())
        return value

    @model_validator(mode="after")
    def _no_overlap(self) -> "FallbackRequest":
        context = {name.casefold() for name in self.context_columns}
        for target in self.targets:
            if target.column.casefold() in context:
                raise ValueError(
                    f"The column '{target.column}' cannot both identify the row and be filled."
                )
        return self

    def to_task(self) -> LookupTask:
        """The task the plan carries: slots are numbered in the order of the request."""
        try:
            return LookupTask(
                question=self.question,
                context=tuple(
                    (f"ctx_{index}", name.strip())
                    for index, name in enumerate(self.context_columns, start=1)
                ),
                columns=tuple(
                    LookupColumn(
                        slot=f"value_{index}",
                        header=target.column.strip(),
                        kind=target.kind,
                        unit=target.unit,
                    )
                    for index, target in enumerate(self.targets, start=1)
                ),
            )
        except ValidationError as exc:
            raise FallbackPlanError(
                f"The lookup task is not valid: {exc.errors()[0]['msg']}."
            ) from exc


# --- The capability for one plan -----------------------------------------------------


def web_lookup_spec(task: LookupTask, context_types: Mapping[str, DataType]) -> CapabilitySpec:
    """The capability as it holds for the plan that carries ``task``.

    ``context_types`` maps each context slot (``ctx_1`` ...) to the type of the column
    bound to it, Text or Number. Outputs are one field per target slot.
    """
    slots = [slot for slot, _ in task.context]
    if set(context_types) != set(slots):
        raise FallbackPlanError("The context types must cover exactly the context slots.")
    if any(kind not in (DataType.TEXT, DataType.NUMBER) for kind in context_types.values()):
        raise FallbackPlanError("A context column must hold text or numbers.")
    inputs: dict[str, InputSpec] = {
        "task": InputSpec(
            type=DataType.TEXT,
            description="The lookup task: the question, the context columns and the targets.",
        )
    }
    for slot, header in task.context:
        inputs[slot] = InputSpec(
            type=context_types[slot],
            description=(
                f"The value of the column '{header}' in the row. It may be empty in a row; "
                "the lookup then runs on the other context values."
            ),
        )
    return CapabilitySpec(
        id=LOOKUP_CAPABILITY,
        version=SPEC_VERSION,
        description=(
            "Ask a model with web search, for each row, for the values of several columns. "
            "The values are found by the model, not verified against a record."
        ),
        inputs=inputs,
        output=OutputSpec(
            type=DataType.TEXT,
            description=(
                "One value per requested column, each with the pages it rests on and "
                "whether a page states it or the model concluded it. A column for which "
                "no traceable value was found stays empty."
            ),
            fields={column.slot: _FIELD_TYPES[column.kind] for column in task.columns},
        ),
    )


def is_fallback_plan(plan: EnrichmentPlan) -> bool:
    """Whether any step uses ``web_lookup``: such a plan is refused on the normal path."""
    return any(step.capability == LOOKUP_CAPABILITY for step in plan.steps)


def _read_plan(plan: EnrichmentPlan) -> tuple[PlanStep, LookupTask]:
    """The step and the task of a well-formed fallback plan; a clear error for any other."""
    if len(plan.steps) != 1:
        raise FallbackPlanError("A fallback plan has exactly one step.")
    step = plan.steps[0]
    if step.capability != LOOKUP_CAPABILITY:
        raise FallbackPlanError(f"The step of a fallback plan uses '{LOOKUP_CAPABILITY}'.")
    if step.parameters:
        raise FallbackPlanError("The web lookup step takes no parameters.")
    text = step.inputs.get("task")
    if not isinstance(text, LiteralValue) or not isinstance(text.value, str):
        raise FallbackPlanError("The step has no lookup task as a text.")
    try:
        task = LookupTask.from_json(text.value)
    except ValueError as exc:
        raise FallbackPlanError("The lookup task of the plan cannot be read.") from exc
    if text.value != task.to_json():
        raise FallbackPlanError("The lookup task of the plan is not in its canonical form.")

    context = dict(task.context)
    if set(step.inputs) != {"task", *context}:
        raise FallbackPlanError("The inputs of the step do not match the context of the task.")
    for slot, header in context.items():
        reference = step.inputs[slot]
        if not isinstance(reference, ColumnReference) or reference.name.strip() != header:
            raise FallbackPlanError(f"The input '{slot}' must be the column '{header}'.")

    headers = {column.slot: column.header for column in task.columns}
    mapped = [output.value_field for output in plan.outputs]
    if sorted(mapped) != sorted(headers):
        raise FallbackPlanError("The outputs do not match the columns of the task.")
    for output in plan.outputs:
        if output.step_id != step.id:
            raise FallbackPlanError("Every output must come from the web lookup step.")
        if output.target_column.strip() != headers[output.value_field]:
            raise FallbackPlanError(
                f"The output '{output.value_field}' must be written to "
                f"'{headers[output.value_field]}'."
            )
        if output.expected_unit is not None:
            raise FallbackPlanError("A web lookup output has no expected unit.")
    return step, task


def fallback_registry(
    plan: EnrichmentPlan,
    columns: Sequence[ColumnProfile],
    offered: CapabilityRegistry | None = None,
) -> CapabilityRegistry:
    """The registry to validate and run THIS plan with.

    It holds what the application offers (``offered``, by default the capabilities of
    ``offered_capabilities`` without the routing one) and the ``web_lookup`` spec derived
    from the plan's own task text. ``columns`` is the table the plan runs on: the types
    of the context inputs are the types of the columns bound to them, which the plan alone
    does not say. Raises ``FallbackPlanError`` for a plan that is not a well-formed
    fallback plan.
    """
    step, task = _read_plan(plan)
    by_name = {column.name: column for column in columns}
    types: dict[str, DataType] = {}
    for slot, _ in task.context:
        reference = step.inputs[slot]
        assert isinstance(reference, ColumnReference)
        column = by_name.get(reference.name)
        if column is None:
            raise FallbackPlanError(f"The column '{reference.name}' does not exist in the table.")
        found = _PRIMITIVE_TYPE_MAP.get(column.inferred_type)
        if found is None:
            raise FallbackPlanError(
                f"The column '{reference.name}' has {column.inferred_type.value} values; "
                "only text and number columns can identify a row."
            )
        types[slot] = found
    registry = CapabilityRegistry()
    specs = offered.list_all() if offered is not None else offered_capabilities(road_distance=False)
    for spec in specs:
        registry.register(spec)
    if registry.get(LOOKUP_CAPABILITY) is not None:
        raise FallbackPlanError(f"The offered capabilities already contain '{LOOKUP_CAPABILITY}'.")
    registry.register(web_lookup_spec(task, types))
    return registry


# --- The plan ------------------------------------------------------------------------


def _describe(column: ColumnProfile) -> str:
    return {
        PrimitiveType.MIXED: "mixed values",
        PrimitiveType.UNKNOWN: "no values to tell its type from",
    }.get(column.inferred_type, f"{column.inferred_type.value} values")


def _near(name: str, names: Sequence[str]) -> str:
    same = [other for other in names if other.casefold() == name.strip().casefold()]
    return f" Did you mean '{same[0]}'?" if same else ""


def build_fallback_plan(
    request: FallbackRequest, columns: Sequence[ColumnProfile]
) -> EnrichmentPlan:
    """The one-step plan for ``request`` on a table with ``columns``.

    The same request on the same columns gives the same plan, byte for byte. Raises
    ``FallbackPlanError`` with a message a user can act on.
    """
    by_name = {column.name: column for column in columns}
    names = list(by_name)

    for name in request.context_columns:
        column = by_name.get(name)
        if column is None:
            raise FallbackPlanError(
                f"The column '{name}' does not exist in the table.{_near(name, names)}"
            )
        if column.inferred_type not in _PRIMITIVE_TYPE_MAP:
            raise FallbackPlanError(
                f"The column '{name}' has {_describe(column)}; only text and number columns "
                "can identify a row."
            )

    for target in request.targets:
        existing = by_name.get(target.column)
        if existing is None:
            if target.column != target.column.strip():
                raise FallbackPlanError(
                    f"The name of the new column '{target.column}' must not start or end "
                    "with a space."
                )
            clash = _near(target.column, names)
            if clash:
                raise FallbackPlanError(
                    f"The new column '{target.column}' differs from an existing column only in "
                    f"letter case.{clash} Use that name to fill it, or choose another name."
                )
            continue
        expected = _FIELD_TYPES[target.kind]
        actual = _PRIMITIVE_TYPE_MAP.get(existing.inferred_type)
        if actual is None:
            if existing.inferred_type not in (PrimitiveType.UNKNOWN, PrimitiveType.MIXED):
                raise FallbackPlanError(
                    f"The column '{target.column}' has {_describe(existing)} and cannot be filled."
                )
        elif actual != expected:
            raise FallbackPlanError(
                f"The column '{target.column}' holds {_describe(existing)}, but it is to be filled "
                f"with {_KIND_WORDS[target.kind]} values."
            )

    task = request.to_task()
    text = task.to_json()
    try:
        if LookupTask.from_json(text) != task:
            raise ValueError
    except ValueError as exc:
        raise FallbackPlanError(
            "The lookup task is too long. Use fewer or shorter columns."
        ) from exc

    inputs: dict[str, Any] = {"task": LiteralValue(kind="literal", value=text)}
    for (slot, _), name in zip(task.context, request.context_columns, strict=True):
        inputs[slot] = ColumnReference(kind="column", name=name)
    plan = EnrichmentPlan(
        version=PLAN_VERSION,
        steps=[PlanStep(id=STEP_ID, capability=LOOKUP_CAPABILITY, inputs=inputs, parameters={})],
        outputs=[
            OutputMapping(step_id=STEP_ID, value_field=column.slot, target_column=target.column)
            for column, target in zip(task.columns, request.targets, strict=True)
        ],
        assumptions=[ASSUMPTION],
    )

    # The application's own validator, as for a planner's plan: a plan it would refuse is
    # a mistake in this module, not something to hand to a user.
    result = PlanValidator(
        fallback_registry(plan, columns),
        DatasetProfile(dataset_id="fallback", name="fallback", columns=list(columns)),
        PlanValidationPolicy(
            max_steps=1, output_targets=frozenset(target.column for target in request.targets)
        ),
    ).validate(plan)
    if not result.valid:
        details = "; ".join(f"{error.code}: {error.message}" for error in result.errors)
        raise FallbackPlanError(f"The fallback plan is not valid: {details}")
    return plan
