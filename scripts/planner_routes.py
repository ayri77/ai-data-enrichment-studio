"""The expected route of each measured planner case, and the check that compares a plan with it.

A plan that passes validation can still be wrong in substance: the right kind of number read
from the wrong place, written into the wrong column, or looked up from a made-up constant. The
validator cannot see that, because it knows capabilities and types, not the task. This module
states, for the eight cases of ``scripts/experiment_planner.py``, which capability must produce
each output column, with which parameters and from which inputs, and reports every way a plan
differs from that.

It judges only those eight cases. It says nothing about a plan for any other table or task, and
it is a measurement aid: the planner, its prompt and the validator do not use it.

``check_route`` is pure. It reads the plan and the column headers, never a cell, and its
messages name steps, targets, capabilities and headers but no table contents (and not the value
of a constant either, which could be a copied cell).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from pydantic import JsonValue

from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    LiteralValue,
    PlanStep,
    StepOutputReference,
)


class DeviationCode(StrEnum):
    WRONG_CAPABILITY = "WRONG_CAPABILITY"  # a target is produced by another capability
    WRONG_FIELD = "WRONG_FIELD"  # right capability, another field of its result
    WRONG_PARAMETER = "WRONG_PARAMETER"  # a parameter that defines the meaning differs
    LITERAL_INPUT = "LITERAL_INPUT"  # an input is a constant where data is expected
    WRONG_SOURCE_COLUMN = "WRONG_SOURCE_COLUMN"  # an input reads another column
    WRONG_UPSTREAM = "WRONG_UPSTREAM"  # an input comes from another kind of source
    MISSING_INPUT = "MISSING_INPUT"  # an input of the expected route is not given
    UNEXPECTED_INPUT = "UNEXPECTED_INPUT"  # an input the expected route does not use
    UNEXPECTED_CAPABILITY = "UNEXPECTED_CAPABILITY"  # a capability the task does not need
    MISSING_TARGET = "MISSING_TARGET"  # an expected output column is not written
    EXTRA_TARGET = "EXTRA_TARGET"  # a column nobody asked for is written
    WRONG_UNIT = "WRONG_UNIT"  # the unit stated on an output mapping differs
    TARGET_HANDED_AWAY = "TARGET_HANDED_AWAY"  # listed as unfilled where a source exists
    TARGET_NOT_HANDED_AWAY = "TARGET_NOT_HANDED_AWAY"  # expected unfilled, but written
    UNUSED_STEP = "UNUSED_STEP"  # a step feeds no output (a failure only if not tolerated)
    OPTIONAL_PARAMETER_SET = "OPTIONAL_PARAMETER_SET"  # an optional parameter, set; a note only


class _Absent:
    """Marks a parameter the plan must not carry."""

    def __repr__(self) -> str:
        return "ABSENT"


ABSENT: Final = _Absent()


@dataclass(frozen=True)
class Optional:
    """The parameter may be absent; one of ``values`` is also fine and is noted, not failed."""

    values: tuple[JsonValue, ...]


@dataclass(frozen=True)
class FromColumn:
    """The input reads this column of the table, named by its header."""

    header: str


@dataclass(frozen=True)
class FromStep:
    """The input is the result of a step of this capability, checked the same way."""

    capability: str
    inputs: Mapping[str, "Source"]
    # Parameters that define the meaning; ABSENT means the plan must not set it.
    parameters: Mapping[str, JsonValue | _Absent | Optional] = field(default_factory=dict)
    # Inputs that may be swapped with each other (the two ends of a symmetric distance).
    interchangeable: tuple[str, ...] = ()


Source = FromColumn | FromStep
# A literal is never acceptable as an input: every input of the six capabilities that the
# measured cases use is data (a name, a place, an entity); the constants (`unit`,
# `property`, ...) are parameters, not inputs, as in every prepared plan.


@dataclass(frozen=True)
class TargetRoute:
    target: str
    field: str
    step: FromStep
    unit: str | None = None  # None: the output mapping states no unit


@dataclass(frozen=True)
class CaseRoute:
    targets: tuple[TargetRoute, ...]
    # Anything else in a plan is a deviation.
    capabilities: frozenset[str]
    # A step that feeds no output is reported; it fails the route only if this is False.
    unused_steps_tolerated: bool = True
    # Targets of the task that no capability can produce: the plan must list them as
    # unfilled and must not write them. Every case of the first six expects none.
    unfilled: tuple[str, ...] = ()


@dataclass(frozen=True)
class Deviation:
    code: DeviationCode
    message: str
    blocking: bool = True


@dataclass(frozen=True)
class RouteVerdict:
    deviations: tuple[Deviation, ...]

    @property
    def on_route(self) -> bool:
        return not any(d.blocking for d in self.deviations)

    @property
    def blocking_codes(self) -> tuple[DeviationCode, ...]:
        return tuple(dict.fromkeys(d.code for d in self.deviations if d.blocking))

    @property
    def note_codes(self) -> tuple[DeviationCode, ...]:
        return tuple(dict.fromkeys(d.code for d in self.deviations if not d.blocking))


# ---------------------------------------------------------------------------------------
# The eight cases. Derived from the prepared plans (demo/*_api_plan.json) for the four that
# have one; written by hand for the other four. The last two have a target nothing can fill.
# ---------------------------------------------------------------------------------------


def _city(city: str, country: str, *, role_open: bool = False) -> FromStep:
    """A city lookup. Where the instruction says "literal cities" there is no required_role;
    where it only says "the city" (the two hand-written cases) the capital role may be set."""
    return FromStep(
        "geocode",
        {"city": FromColumn(city), "country": FromColumn(country)},
        {"required_role": Optional(("country_capital",)) if role_open else ABSENT},
    )


_FROM = _city("Capital_From", "Country_From")
_TO = _city("Capital_To", "Country_To")
_FROM_OPEN = _city("Capital_From", "Country_From", role_open=True)
_PAIR = {"point_a": _FROM, "point_b": _TO}
_GEODESIC = FromStep(
    "geodesic_distance", _PAIR, {"unit": "km"}, interchangeable=("point_a", "point_b")
)
_ROAD = FromStep("road_distance", _PAIR, {"unit": "km"})
_RATIO = FromStep("ratio", {"numerator": _ROAD, "denominator": _GEODESIC})
_MOUNTAIN = FromStep(
    "lookup_property",
    {
        "entity": FromStep(
            "resolve_entity",
            {"name": FromColumn("Mountain"), "country": FromColumn("Country")},
            {"entity_type": "mountain"},
        )
    },
    {"property": "elevation"},
)
_HEIGHT = TargetRoute("height", "value", _MOUNTAIN, unit="m")
_DISTANCE = TargetRoute("distance", "value", _GEODESIC, unit="km")

ROUTES: Final[Mapping[str, CaseRoute]] = {
    "capitals": CaseRoute(
        targets=(_DISTANCE,),
        capabilities=frozenset({"geocode", "geodesic_distance"}),
    ),
    "mountains": CaseRoute(
        targets=(_HEIGHT,),
        capabilities=frozenset({"resolve_entity", "lookup_property"}),
    ),
    "mountains_gaps": CaseRoute(
        targets=(_HEIGHT,),
        capabilities=frozenset({"resolve_entity", "lookup_property"}),
    ),
    "capitals_roads": CaseRoute(
        targets=(
            _DISTANCE,
            TargetRoute("road", "value", _ROAD, unit="km"),
            TargetRoute("detour", "value", _RATIO),
        ),
        capabilities=frozenset({"geocode", "geodesic_distance", "road_distance", "ratio"}),
    ),
    "capitals_coordinates": CaseRoute(
        targets=(
            TargetRoute("latitude", "latitude", _FROM_OPEN),
            TargetRoute("longitude", "longitude", _FROM_OPEN),
        ),
        capabilities=frozenset({"geocode"}),
    ),
    "capitals_population": CaseRoute(
        targets=(TargetRoute("population", "population", _FROM_OPEN),),
        capabilities=frozenset({"geocode"}),
    ),
    # The height has its route; no capability knows the geological age of a mountain.
    "mountains_age": CaseRoute(
        targets=(_HEIGHT,),
        capabilities=frozenset({"resolve_entity", "lookup_property"}),
        unfilled=("age",),
    ),
    # The distance has its route; no capability knows who is the mayor of a city.
    "capitals_mayor": CaseRoute(
        targets=(_DISTANCE,),
        capabilities=frozenset({"geocode", "geodesic_distance"}),
        unfilled=("mayor",),
    ),
}


# ---------------------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------------------


def check_route(
    plan: EnrichmentPlan,
    expectation: CaseRoute,
    columns: Sequence[str],
    unfilled: Sequence[str] = (),
) -> RouteVerdict:
    """Compare a plan that passed validation with the expected route of its case.

    ``columns`` are the headers of the table the plan was made for. An expectation that names
    a header the table does not have is a mistake in the expectation, not in the plan, and
    raises ``ValueError``.

    ``unfilled`` are the targets the planner named as having no source. A plan is on the
    expected route when the targets it writes pass their routes and this set equals the one
    the case expects. A target handed away where a capability can produce it is
    ``TARGET_HANDED_AWAY`` (and not also a missing target); a target expected to be handed
    away that the plan writes, or does not list, is ``TARGET_NOT_HANDED_AWAY`` (and not also
    an extra target; how it was written is not judged).
    """
    for header in sorted(_headers(expectation) - set(columns)):
        raise ValueError(f"The expected route reads a column that the table lacks: {header}")
    steps = {step.id: step for step in plan.steps}
    found: list[Deviation] = []

    for step in plan.steps:
        if step.capability not in expectation.capabilities:
            found.append(
                _dev(
                    DeviationCode.UNEXPECTED_CAPABILITY,
                    f"step {step.id} uses {step.capability}, which this task does not need",
                )
            )

    wanted = {route.target: route for route in expectation.targets}
    handed_away = set(unfilled)
    for target in sorted(handed_away - set(expectation.unfilled)):
        found.append(
            _dev(
                DeviationCode.TARGET_HANDED_AWAY,
                f"column {target} is listed as having no source, but the task has one for it",
            )
        )
    written: set[str] = set()
    for output in plan.outputs:
        route = wanted.get(output.target_column)
        if output.target_column in expectation.unfilled:
            found.append(
                _dev(
                    DeviationCode.TARGET_NOT_HANDED_AWAY,
                    f"column {output.target_column} has no source but is written",
                )
            )
            continue
        if route is None:
            found.append(
                _dev(
                    DeviationCode.EXTRA_TARGET,
                    f"column {output.target_column} is written but was not asked for",
                )
            )
            continue
        written.add(output.target_column)
        producer = steps.get(output.step_id)
        label = f"{output.target_column}"
        if producer is None:
            found.append(
                _dev(DeviationCode.WRONG_UPSTREAM, f"{label} reads a step that does not exist")
            )
        elif producer.capability != route.step.capability:
            found.append(
                _dev(
                    DeviationCode.WRONG_CAPABILITY,
                    f"{label} is produced by {producer.capability}.{output.value_field}, "
                    f"expected {route.step.capability}.{route.field}",
                )
            )
        else:
            if output.value_field != route.field:
                found.append(
                    _dev(
                        DeviationCode.WRONG_FIELD,
                        f"{label} is read from {producer.capability}.{output.value_field}, "
                        f"expected {route.step.capability}.{route.field}",
                    )
                )
            found.extend(_check_step(producer, route.step, steps, columns, ()))
        if output.expected_unit != route.unit:
            found.append(
                _dev(
                    DeviationCode.WRONG_UNIT,
                    f"{label} states unit {output.expected_unit!r}, expected {route.unit!r}",
                )
            )
    for target in expectation.unfilled:
        if target not in handed_away and target not in {o.target_column for o in plan.outputs}:
            found.append(
                _dev(
                    DeviationCode.TARGET_NOT_HANDED_AWAY,
                    f"column {target} has no source but is not listed as such",
                )
            )
    for route in expectation.targets:
        if route.target not in written and route.target not in handed_away:
            found.append(
                _dev(DeviationCode.MISSING_TARGET, f"column {route.target} is not written")
            )

    reached = _reached({output.step_id for output in plan.outputs}, steps)
    for step in plan.steps:
        if step.id not in reached:
            found.append(
                Deviation(
                    DeviationCode.UNUSED_STEP,
                    f"step {step.id} feeds no output",
                    blocking=not expectation.unused_steps_tolerated,
                )
            )
    return RouteVerdict(tuple(dict.fromkeys(found)))


def _dev(code: DeviationCode, message: str) -> Deviation:
    return Deviation(code, message)


def _headers(expectation: CaseRoute) -> set[str]:
    def walk(step: FromStep) -> set[str]:
        headers: set[str] = set()
        for source in step.inputs.values():
            headers |= {source.header} if isinstance(source, FromColumn) else walk(source)
        return headers

    return set().union(*(walk(route.step) for route in expectation.targets))


def _reached(start: set[str], steps: Mapping[str, PlanStep]) -> set[str]:
    reached: set[str] = set()
    pending = [step_id for step_id in start if step_id in steps]
    while pending:
        step_id = pending.pop()
        if step_id in reached:
            continue
        reached.add(step_id)
        pending.extend(
            ref.step_id
            for ref in steps[step_id].inputs.values()
            if isinstance(ref, StepOutputReference) and ref.step_id in steps
        )
    return reached


def _check_step(
    step: PlanStep,
    expected: FromStep,
    steps: Mapping[str, PlanStep],
    columns: Sequence[str],
    path: tuple[str, ...],
) -> list[Deviation]:
    """Parameters and inputs of a step whose capability is already the expected one."""
    found: list[Deviation] = []
    for name, wanted in expected.parameters.items():
        given = step.parameters.get(name, ABSENT)
        if isinstance(wanted, Optional):
            if isinstance(given, _Absent):
                continue
            if given in wanted.values:
                found.append(
                    Deviation(
                        DeviationCode.OPTIONAL_PARAMETER_SET,
                        f"step {step.id} sets {name}, which the task leaves open",
                        blocking=False,
                    )
                )
            else:
                found.append(
                    _dev(
                        DeviationCode.WRONG_PARAMETER,
                        f"step {step.id} sets {name}={given!r}, expected it absent "
                        f"or one of {list(wanted.values)!r}",
                    )
                )
        elif isinstance(wanted, _Absent):
            if not isinstance(given, _Absent):
                found.append(
                    _dev(
                        DeviationCode.WRONG_PARAMETER,
                        f"step {step.id} sets {name}, which this task does not use",
                    )
                )
        elif isinstance(given, _Absent):
            found.append(
                _dev(
                    DeviationCode.WRONG_PARAMETER,
                    f"step {step.id} does not set {name}, expected {wanted!r}",
                )
            )
        elif given != wanted:
            found.append(
                _dev(
                    DeviationCode.WRONG_PARAMETER,
                    f"step {step.id} sets {name}={given!r}, expected {wanted!r}",
                )
            )

    here = (*path, step.id)
    straight = _check_inputs(step, expected.inputs, steps, columns, here)
    best = straight
    if len(expected.interchangeable) == 2 and any(d.blocking for d in straight):
        first, second = expected.interchangeable
        swapped_inputs = {**expected.inputs}
        swapped_inputs[first], swapped_inputs[second] = (
            expected.inputs[second],
            expected.inputs[first],
        )
        swapped = _check_inputs(step, swapped_inputs, steps, columns, here)
        if sum(d.blocking for d in swapped) < sum(d.blocking for d in straight):
            best = swapped
    return [*found, *best]


def _check_inputs(
    step: PlanStep,
    expected: Mapping[str, Source],
    steps: Mapping[str, PlanStep],
    columns: Sequence[str],
    path: tuple[str, ...],
) -> list[Deviation]:
    found: list[Deviation] = []
    for name, source in expected.items():
        ref = step.inputs.get(name)
        if ref is None:
            found.append(_dev(DeviationCode.MISSING_INPUT, f"step {step.id} gives no input {name}"))
        elif isinstance(ref, LiteralValue):
            # Never name the value: a constant copied from a cell would be table content.
            found.append(
                _dev(
                    DeviationCode.LITERAL_INPUT,
                    f"step {step.id} takes input {name} from a constant "
                    f"({_kind(ref.value)}) instead of {_describe(source)}",
                )
            )
        elif isinstance(source, FromColumn):
            found.extend(_check_column(step, name, ref, source, columns))
        else:
            found.extend(_check_upstream(step, name, ref, source, steps, columns, path))
    for name in step.inputs:
        if name not in expected:
            found.append(
                _dev(
                    DeviationCode.UNEXPECTED_INPUT,
                    f"step {step.id} gives input {name}, which this task does not use",
                )
            )
    return found


def _check_column(
    step: PlanStep,
    name: str,
    ref: ColumnReference | StepOutputReference,
    source: FromColumn,
    columns: Sequence[str],
) -> list[Deviation]:
    if isinstance(ref, StepOutputReference):
        return [
            _dev(
                DeviationCode.WRONG_UPSTREAM,
                f"step {step.id} takes input {name} from the output of step {ref.step_id}, "
                f"expected {_describe(source)}",
            )
        ]
    if ref.name == source.header:
        return []
    known = "" if ref.name in columns else " (not a column of the table)"
    return [
        _dev(
            DeviationCode.WRONG_SOURCE_COLUMN,
            f"step {step.id} reads input {name} from column {ref.name}{known}, "
            f"expected {source.header}",
        )
    ]


def _check_upstream(
    step: PlanStep,
    name: str,
    ref: ColumnReference | StepOutputReference,
    source: FromStep,
    steps: Mapping[str, PlanStep],
    columns: Sequence[str],
    path: tuple[str, ...],
) -> list[Deviation]:
    where = f"step {step.id} takes input {name}"
    if isinstance(ref, ColumnReference):
        return [
            _dev(
                DeviationCode.WRONG_UPSTREAM,
                f"{where} from the column {ref.name}, expected {_describe(source)}",
            )
        ]
    upstream = steps.get(ref.step_id)
    if upstream is None or upstream.id in path:
        return [
            _dev(
                DeviationCode.WRONG_UPSTREAM,
                f"{where} from step {ref.step_id}, which is not a usable source",
            )
        ]
    if upstream.capability != source.capability:
        return [
            _dev(
                DeviationCode.WRONG_UPSTREAM,
                f"{where} from step {upstream.id} ({upstream.capability}), "
                f"expected {_describe(source)}",
            )
        ]
    return _check_step(upstream, source, steps, columns, path)


def _describe(source: Source) -> str:
    if isinstance(source, FromColumn):
        return f"the column {source.header}"
    return f"the result of a {source.capability} step"


def _kind(value: JsonValue) -> str:
    if isinstance(value, str):
        return f"text of {len(value)} characters"
    return type(value).__name__
