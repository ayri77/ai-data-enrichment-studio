"""The text extraction: a plan the server builds, never the planner.

A table row may carry a text (a mail, for instance). The user may start a plan that has one
step, the capability ``text_extraction``: for each row a model reads that text and answers
several target columns. Nothing here calls a model or a service. This module holds

* ``ExtractionRequest``: what the user chose (the task, the text column, the targets);
* ``build_extraction_plan``: the plan that follows from it, the same bytes every time;
* ``text_extraction_spec`` and ``extraction_registry``: the capability as it holds for ONE plan.

It is built by the same pattern as the web lookup fallback (``capabilities/fallback.py``) with
one difference: there is no mixed plan. The capability is not part of ``offered_capabilities``
and not of ``BINDINGS``, and the planner is never told about it. Its contract depends on the
plan (one output field per target), so it is derived from the plan's own task text.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ai_data_enrichment.capabilities.fallback import (
    FallbackPlanError,
    _check_target,  # pyright: ignore[reportPrivateUsage]
    _column_name,  # pyright: ignore[reportPrivateUsage]
    _describe,  # pyright: ignore[reportPrivateUsage]
    _has_control,  # pyright: ignore[reportPrivateUsage]
    _near,  # pyright: ignore[reportPrivateUsage]
    _same,  # pyright: ignore[reportPrivateUsage]
)
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
from ai_data_enrichment.providers.text_extraction import (
    EXTRACTION_CAPABILITY,
    MAX_COLUMNS,
    MAX_OPTION,
    MAX_OPTIONS,
    MAX_QUESTION,
    ExtractionColumn,
    ExtractionTask,
    parse_task,
)
from ai_data_enrichment.validation.plan_validator import (
    PlanValidationPolicy,
    PlanValidator,
)

__all__ = [
    "EXTRACTION_CAPABILITY",
    "ExtractionPlanError",
    "ExtractionRequest",
    "ExtractionTarget",
    "build_extraction_plan",
    "extraction_registry",
    "is_extraction_plan",
    "text_extraction_spec",
]

# What a plan may be in bytes: the limit of ``plan_run.MAX_PLAN_BYTES``, which a test keeps equal.
# A task of 20 columns with 20 options each can pass the limit of its own text in characters
# and still be larger than this in bytes, so the plan is measured too.
MAX_PLAN_BYTES = 64 * 1024
STEP_ID = "extract"
PLAN_VERSION = "1.0"
SPEC_VERSION = "1.0"
ASSUMPTION = (
    "Each value is read by an AI model from the text of its own row, one request per row, "
    "and kept only where the passage it quotes is in that text. The values are not "
    "verified against anything else."
)

TargetKind = Literal["text", "number", "date", "yes_no", "one_of"]
# A date is written as YYYY-MM-DD text: the workbook writer takes only text and numbers.
_FIELD_TYPES: Mapping[str, DataType] = {
    "text": DataType.TEXT,
    "number": DataType.NUMBER,
    "date": DataType.TEXT,
    "yes_no": DataType.TEXT,
    "one_of": DataType.TEXT,
}
_KIND_WORDS: Mapping[str, str] = {
    "text": "text",
    "number": "number",
    "date": "date",
    "yes_no": "yes/no",
    "one_of": "choice",
}


class ExtractionPlanError(ValueError):
    """A request or a plan the extraction cannot accept; the message is meant for a user."""


class ExtractionTarget(BaseModel):
    """One column to fill: an existing header or the name of a new column."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    column: str
    kind: TargetKind
    # The answers a ``one_of`` column may hold; none for any other kind.
    options: tuple[str, ...] = ()

    @field_validator("column")
    @classmethod
    def _column(cls, value: str) -> str:
        return _column_name(value, "target")

    @model_validator(mode="after")
    def _options_only_for_one_of(self) -> "ExtractionTarget":
        if self.kind != "one_of":
            if self.options:
                raise ValueError(
                    f"Options are only for a choice column; '{self.column}' is "
                    f"{_KIND_WORDS[self.kind]}."
                )
            return self
        if not 2 <= len(self.options) <= MAX_OPTIONS:
            raise ValueError(
                f"The choice column '{self.column}' needs 2 to {MAX_OPTIONS} different options."
            )
        for option in self.options:
            if not option.strip() or _has_control(option) or len(option.strip()) > MAX_OPTION:
                raise ValueError(
                    f"An option of '{self.column}' must be a short plain text of at most "
                    f"{MAX_OPTION} characters."
                )
        if len({option.strip().casefold() for option in self.options}) != len(self.options):
            raise ValueError(f"The options of '{self.column}' must be different.")
        return self


class ExtractionRequest(BaseModel):
    """What the user asks a model to read out of the text of every row.

    At most 20 targets with at most 20 options of 60 characters each, the limits of an
    extraction task; ``build_extraction_plan`` refuses a request whose task text is still
    too long for the plan.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    text_column: str
    targets: tuple[ExtractionTarget, ...]

    @field_validator("question")
    @classmethod
    def _question(cls, value: str) -> str:
        # Stricter than the web lookup's question: the provider takes no line break.
        if _has_control(value):
            raise ValueError("The task must not contain control characters.")
        if not value.strip():
            raise ValueError("The task must not be blank.")
        if len(value.strip()) > MAX_QUESTION:
            raise ValueError(f"The task may have at most {MAX_QUESTION} characters.")
        return value.strip()

    @field_validator("text_column")
    @classmethod
    def _text_column(cls, value: str) -> str:
        return _column_name(value, "text")

    @field_validator("targets")
    @classmethod
    def _targets(cls, value: tuple[ExtractionTarget, ...]) -> tuple[ExtractionTarget, ...]:
        if not 1 <= len(value) <= MAX_COLUMNS:
            raise ValueError(f"Choose 1 to {MAX_COLUMNS} columns to fill.")
        seen: set[str] = set()
        for target in value:
            if target.column.strip().casefold() in seen:
                raise ValueError(f"The column '{target.column}' is to be filled twice.")
            seen.add(target.column.strip().casefold())
        return value

    @model_validator(mode="after")
    def _text_is_not_a_target(self) -> "ExtractionRequest":
        for target in self.targets:
            if _same(target.column, self.text_column):
                raise ValueError(
                    f"The column '{target.column}' cannot both hold the text and be filled."
                )
        return self

    def to_task(self) -> ExtractionTask:
        """The task the plan carries: slots are numbered in the order of the request."""
        try:
            return ExtractionTask(
                self.question,
                tuple(
                    ExtractionColumn(target.column.strip(), target.kind, target.options)
                    for target in self.targets
                ),
            )
        except ValueError as exc:
            raise ExtractionPlanError(f"The extraction task is not valid: {exc}.") from exc


# --- The capability for one plan -----------------------------------------------------


def text_extraction_spec(task: ExtractionTask) -> CapabilitySpec:
    """The capability as it holds for the plan that carries ``task``.

    Outputs are one field per target slot: a number is a Number, every other kind is Text
    (a date as ``YYYY-MM-DD``).
    """
    return CapabilitySpec(
        id=EXTRACTION_CAPABILITY,
        version=SPEC_VERSION,
        description=(
            "Ask a model, for each row, to read the values of several columns out of the "
            "text of that row. A value is kept only where the passage it quotes is in the "
            "text; it is not verified against anything else."
        ),
        inputs={
            "task": InputSpec(
                type=DataType.TEXT,
                description="The extraction task: what is asked and the columns to fill.",
            ),
            "text": InputSpec(
                type=DataType.TEXT,
                description=(
                    "The text of the row to read. A row without text is not read and the "
                    "step fails for that row."
                ),
            ),
        },
        output=OutputSpec(
            type=DataType.TEXT,
            description=(
                "One value per requested column, each with the passage of the text it rests "
                "on and whether the passage says it as it is or it follows from it. A column "
                "the text does not say stays empty."
            ),
            fields={slot: _FIELD_TYPES[column.kind] for slot, column in task.slots},
        ),
    )


def is_extraction_plan(plan: EnrichmentPlan) -> bool:
    """Whether any step uses ``text_extraction``: such a plan is refused on the normal path."""
    return any(step.capability == EXTRACTION_CAPABILITY for step in plan.steps)


def _read_plan(plan: EnrichmentPlan) -> tuple[PlanStep, ExtractionTask]:
    """The extraction step and its task of a well-formed plan; a clear error otherwise."""
    if len(plan.steps) != 1:
        raise ExtractionPlanError("An extraction plan has exactly one step and no other.")
    step = plan.steps[0]
    if step.capability != EXTRACTION_CAPABILITY:
        raise ExtractionPlanError(f"The step of an extraction plan uses '{EXTRACTION_CAPABILITY}'.")
    if step.parameters:
        raise ExtractionPlanError("The extraction step takes no parameters.")
    literal = step.inputs.get("task")
    if not isinstance(literal, LiteralValue) or not isinstance(literal.value, str):
        raise ExtractionPlanError("The step has no extraction task as a text.")
    # Only the canonical text is a task, so what a plan says is exactly what runs.
    task = parse_task(literal.value)
    if task is None:
        raise ExtractionPlanError("The extraction task of the plan cannot be read.")
    if set(step.inputs) != {"task", "text"}:
        raise ExtractionPlanError("The inputs of the step must be the task and the text.")
    text = step.inputs["text"]
    if not isinstance(text, ColumnReference):
        raise ExtractionPlanError("The input 'text' must be a column.")

    headers = {slot: column.header for slot, column in task.slots}
    if sorted(output.value_field for output in plan.outputs) != sorted(headers):
        raise ExtractionPlanError("The outputs do not match the columns of the task.")
    for output in plan.outputs:
        if output.step_id != step.id:
            raise ExtractionPlanError("Every output must come from the extraction step.")
        if output.target_column.strip() != headers[output.value_field]:
            raise ExtractionPlanError(
                f"The output '{output.value_field}' must be written to "
                f"'{headers[output.value_field]}'."
            )
        if output.expected_unit is not None:
            raise ExtractionPlanError("An extraction output has no expected unit.")
        if _same(output.target_column, text.name):
            raise ExtractionPlanError(
                f"The column '{text.name.strip()}' holds the text to read but the plan writes "
                "to it; its cells may change during the run."
            )
    return step, task


def extraction_registry(
    plan: EnrichmentPlan, columns: Sequence[ColumnProfile]
) -> CapabilityRegistry:
    """The registry to validate and run THIS plan with.

    It holds the ``text_extraction`` spec derived from the plan's own task text and nothing
    else: the capabilities the planner is offered are not part of an extraction plan.
    ``columns`` is the table the plan runs on, to check that the text column is a text
    column. Exactly one step, of this capability, is well formed; any other plan (a second
    step, a web lookup step or a planner capability next to it) raises ``ExtractionPlanError``.
    """
    step, task = _read_plan(plan)
    reference = step.inputs["text"]
    assert isinstance(reference, ColumnReference)
    column = next((item for item in columns if item.name == reference.name), None)
    if column is None:
        raise ExtractionPlanError(f"The column '{reference.name}' does not exist in the table.")
    if column.inferred_type is not PrimitiveType.TEXT:
        raise ExtractionPlanError(
            f"The column '{reference.name}' has {_describe(column)}; only a text column can "
            "be read."
        )
    registry = CapabilityRegistry()
    registry.register(text_extraction_spec(task))
    return registry


# --- The plan ------------------------------------------------------------------------


def _check_request(request: ExtractionRequest, columns: Sequence[ColumnProfile]) -> None:
    """Refuse a request the table cannot serve, with a message a user can act on."""
    by_name = {column.name: column for column in columns}
    names = list(by_name)

    text = by_name.get(request.text_column)
    if text is None:
        raise ExtractionPlanError(
            f"The column '{request.text_column}' does not exist in the table."
            f"{_near(request.text_column, names)}"
        )
    if text.inferred_type is not PrimitiveType.TEXT:
        raise ExtractionPlanError(
            f"The column '{request.text_column}' has {_describe(text)}; only a text column can "
            "be read."
        )

    for target in request.targets:
        if _same(target.column, request.text_column):
            raise ExtractionPlanError(
                f"The column '{target.column}' cannot both hold the text and be filled."
            )
        try:
            _check_target(
                target.column, _FIELD_TYPES[target.kind], _KIND_WORDS[target.kind], by_name, names
            )
        except FallbackPlanError as exc:
            raise ExtractionPlanError(str(exc)) from exc


def build_extraction_plan(
    request: ExtractionRequest, columns: Sequence[ColumnProfile]
) -> EnrichmentPlan:
    """The one-step plan for ``request`` on a table with ``columns``.

    The same request on the same columns gives the same plan, byte for byte. The plan is
    checked with the application's own validator against ``extraction_registry``. Raises
    ``ExtractionPlanError`` with a message a user can act on.
    """
    _check_request(request, columns)
    task = request.to_task()
    text = task.to_json()
    if parse_task(text) != task:
        raise ExtractionPlanError("The extraction task is too long. Use fewer or shorter columns.")

    inputs: dict[str, Any] = {
        "task": LiteralValue(kind="literal", value=text),
        "text": ColumnReference(kind="column", name=request.text_column),
    }
    step = PlanStep(id=STEP_ID, capability=EXTRACTION_CAPABILITY, inputs=inputs, parameters={})
    outputs = [
        OutputMapping(step_id=STEP_ID, value_field=slot, target_column=target.column)
        for (slot, _), target in zip(task.slots, request.targets, strict=True)
    ]
    plan = EnrichmentPlan(
        version=PLAN_VERSION, steps=[step], outputs=outputs, assumptions=[ASSUMPTION]
    )
    if len(plan.model_dump_json().encode()) > MAX_PLAN_BYTES:
        raise ExtractionPlanError("The extraction plan is too long. Use fewer or shorter columns.")
    _validated(plan, columns, targets=frozenset(target.column for target in request.targets))
    return plan


def _validated(
    plan: EnrichmentPlan, columns: Sequence[ColumnProfile], *, targets: frozenset[str]
) -> None:
    """The application's own validator, as for a planner's plan: a plan it would refuse is
    a mistake in this module, not something to hand to a user."""
    result = PlanValidator(
        extraction_registry(plan, columns),
        DatasetProfile(dataset_id="extraction", name="extraction", columns=list(columns)),
        PlanValidationPolicy(max_steps=1, output_targets=targets),
    ).validate(plan)
    if not result.valid:
        details = "; ".join(f"{error.code}: {error.message}" for error in result.errors)
        raise ExtractionPlanError(f"The extraction plan is not valid: {details}")
