"""The route check: a valid plan is compared with the expected route of its case, offline."""

import copy
from collections.abc import Callable
from typing import Any

import pytest
from scripts.experiment_planner import INSTRUCTIONS
from scripts.planner_routes import (
    ABSENT,
    ROUTES,
    CaseRoute,
    DeviationCode,
    FromColumn,
    FromStep,
    Optional,
    TargetRoute,
    check_route,
)
from tests.support.planning import workbook_profile

from ai_data_enrichment import sample_catalog
from ai_data_enrichment.capabilities.definitions import offered_capabilities
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan

Plan = dict[str, Any]
C = DeviationCode


def _geocode(step_id: str, city: str, country: str) -> Plan:
    return {
        "id": step_id,
        "capability": "geocode",
        "inputs": {
            "city": {"kind": "column", "name": city},
            "country": {"kind": "column", "name": country},
        },
        "parameters": {},
    }


def _by_hand(outputs: list[tuple[str, str]]) -> Plan:
    """The two cases without a prepared plan: one geocode step, fields written out."""
    return {
        "version": "1.0",
        "steps": [_geocode("place", "Capital_From", "Country_From")],
        "outputs": [
            {"step_id": "place", "value_field": field, "target_column": target}
            for target, field in outputs
        ],
        "assumptions": [],
    }


def known_good(case: str) -> Plan:
    if case == "capitals_coordinates":
        return _by_hand([("latitude", "latitude"), ("longitude", "longitude")])
    if case == "capitals_population":
        return _by_hand([("population", "population")])
    sample = sample_catalog.find(case)
    assert sample is not None
    plan = sample_catalog.prepared_plan(sample)
    assert plan is not None
    return plan


def columns(case: str) -> list[str]:
    return [column.name for column in workbook_profile(case).columns]


def verdict(case: str, plan: Plan):
    return check_route(EnrichmentPlan.model_validate(plan), ROUTES[case], columns(case))


def blocking(case: str, plan: Plan) -> set[DeviationCode]:
    return set(verdict(case, plan).blocking_codes)


def step(plan: Plan, step_id: str) -> Plan:
    return next(s for s in plan["steps"] if s["id"] == step_id)


def output(plan: Plan, target: str) -> Plan:
    return next(o for o in plan["outputs"] if o["target_column"] == target)


# The six cases whose every target has a source; the two with a target nothing can fill are
# tested apart, below.
CASES = tuple(case for case in INSTRUCTIONS if not ROUTES[case].unfilled)
PARTIAL_CASES = {"mountains_age": ("mountains", "age"), "capitals_mayor": ("capitals", "mayor")}


def test_every_measured_case_has_a_route() -> None:
    assert set(ROUTES) == set(INSTRUCTIONS)


@pytest.mark.parametrize("sample", sample_catalog.SAMPLES, ids=lambda s: s.sample_id)
def test_every_prepared_plan_passes_the_route_check_of_its_case(
    sample: sample_catalog.Sample,
) -> None:
    plan = sample_catalog.prepared_plan(sample)
    assert plan is not None
    result = verdict(sample.sample_id, plan)
    assert result.deviations == ()
    assert result.on_route


@pytest.mark.parametrize("case", CASES)
def test_the_known_good_plan_of_each_case_is_on_the_expected_route(case: str) -> None:
    assert verdict(case, known_good(case)).deviations == ()


def test_two_geocode_steps_for_latitude_and_longitude_are_as_good_as_one() -> None:
    plan = known_good("capitals_coordinates")
    plan["steps"].append(_geocode("again", "Capital_From", "Country_From"))
    output(plan, "longitude")["step_id"] = "again"
    assert verdict("capitals_coordinates", plan).deviations == ()


def test_the_two_ends_of_a_symmetric_distance_may_be_swapped() -> None:
    plan = known_good("capitals")
    inputs = step(plan, "distance")["inputs"]
    inputs["point_a"], inputs["point_b"] = inputs["point_b"], inputs["point_a"]
    assert verdict("capitals", plan).deviations == ()


def test_the_expectations_agree_with_the_capability_definitions() -> None:
    specs = {spec.id: spec for spec in offered_capabilities(road_distance=True)}

    def walk(expected: FromStep) -> None:
        spec = specs[expected.capability]
        assert set(expected.inputs) <= set(spec.inputs)
        assert {n for n, i in spec.inputs.items() if i.required} <= set(expected.inputs)
        for name, value in expected.parameters.items():
            allowed = spec.parameters[name].allowed_values
            assert name in spec.parameters
            if value is not ABSENT and allowed:
                for each in value.values if isinstance(value, Optional) else (value,):
                    assert each in allowed
        for source in expected.inputs.values():
            if isinstance(source, FromStep):
                walk(source)

    for case, route in ROUTES.items():
        assert route.capabilities <= set(specs)
        for target in route.targets:
            assert target.step.capability in route.capabilities
            assert target.field in (specs[target.step.capability].output.fields or {})
            walk(target.step)
        # Every parameter the handlers demand is part of the meaning, so it is expected.
        required = {
            (spec.id, name)
            for spec in specs.values()
            if spec.id in route.capabilities
            for name, parameter in spec.parameters.items()
            if parameter.required
        }
        stated = _parameters_stated(route)
        assert required <= stated, case


def _parameters_stated(route: CaseRoute) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()

    def walk(expected: FromStep) -> None:
        found.update((expected.capability, name) for name in expected.parameters)
        for source in expected.inputs.values():
            if isinstance(source, FromStep):
                walk(source)

    for target in route.targets:
        walk(target.step)
    return found


def test_an_expectation_that_names_a_missing_header_is_an_error_not_a_deviation() -> None:
    route = CaseRoute(
        targets=(TargetRoute("x", "value", FromStep("geocode", {"city": FromColumn("Nope")})),),
        capabilities=frozenset({"geocode"}),
    )
    plan = EnrichmentPlan.model_validate(known_good("capitals_population"))
    with pytest.raises(ValueError, match="Nope"):
        check_route(plan, route, columns("capitals"))


# --- hand-made variants: each one triggers exactly the intended code ---------------------


def _rename_target(plan: Plan) -> None:
    plan["outputs"][0]["target_column"] = "age"


def _drop_target(plan: Plan) -> None:
    del plan["outputs"][-1]


def _literal_name(plan: Plan) -> None:
    for s in plan["steps"]:
        for name, ref in s["inputs"].items():
            if ref["kind"] == "column":
                s["inputs"][name] = {"kind": "literal", "value": "fake_age_entity"}
                return


def _other_column(plan: Plan) -> None:
    for s in plan["steps"]:
        for ref in s["inputs"].values():
            if ref["kind"] == "column":
                ref["name"] = "Country_To" if ref["name"] != "Country_To" else "Capital_To"
                return


OUTSIDE = {
    "capitals": "resolve_entity",
    "mountains": "geocode",
    "mountains_gaps": "geocode",
    "capitals_roads": "resolve_entity",
    "capitals_coordinates": "geodesic_distance",
    "capitals_population": "lookup_property",
}


def _extra_capability(case: str) -> Callable[[Plan], None]:
    def mutate(plan: Plan) -> None:
        plan["steps"].append(
            {"id": "extra", "capability": OUTSIDE[case], "inputs": {}, "parameters": {}}
        )

    return mutate


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize(
    ("variant", "mutate", "codes"),
    [
        ("renamed target", _rename_target, {C.MISSING_TARGET, C.EXTRA_TARGET}),
        ("missing target", _drop_target, {C.MISSING_TARGET}),
        ("constant name", _literal_name, {C.LITERAL_INPUT}),
        ("other column", _other_column, {C.WRONG_SOURCE_COLUMN}),
    ],
)
def test_each_variant_triggers_exactly_its_code(
    case: str, variant: str, mutate: Callable[[Plan], None], codes: set[DeviationCode]
) -> None:
    plan = copy.deepcopy(known_good(case))
    mutate(plan)
    assert blocking(case, plan) == codes, variant


@pytest.mark.parametrize("case", CASES)
def test_a_capability_the_task_does_not_need_is_unexpected_and_its_idle_step_is_noted(
    case: str,
) -> None:
    plan = copy.deepcopy(known_good(case))
    _extra_capability(case)(plan)
    result = verdict(case, plan)
    assert set(result.blocking_codes) == {C.UNEXPECTED_CAPABILITY}
    assert set(result.note_codes) == {C.UNUSED_STEP}
    assert not result.on_route


@pytest.mark.parametrize("case", CASES)
def test_a_unit_other_than_the_expected_one_is_a_wrong_unit(case: str) -> None:
    plan = copy.deepcopy(known_good(case))
    plan["outputs"][0]["expected_unit"] = "mi"
    assert blocking(case, plan) == {C.WRONG_UNIT}


def test_a_stated_unit_where_the_route_has_none_is_a_wrong_unit() -> None:
    plan = known_good("capitals_roads")
    output(plan, "detour")["expected_unit"] = "km"
    assert blocking("capitals_roads", plan) == {C.WRONG_UNIT}
    plan = known_good("capitals")
    del output(plan, "distance")["expected_unit"]
    assert blocking("capitals", plan) == {C.WRONG_UNIT}


@pytest.mark.parametrize("case", CASES)
def test_an_unused_step_of_an_allowed_capability_is_reported_and_does_not_fail(
    case: str,
) -> None:
    plan = copy.deepcopy(known_good(case))
    plan["steps"].append({**copy.deepcopy(plan["steps"][0]), "id": "idle"})
    result = verdict(case, plan)
    assert result.on_route
    assert [d.code for d in result.deviations] == [C.UNUSED_STEP]
    assert not result.deviations[0].blocking
    assert "idle" in result.deviations[0].message


def test_unused_steps_fail_where_the_case_does_not_tolerate_them() -> None:
    strict = CaseRoute(
        ROUTES["capitals_population"].targets,
        ROUTES["capitals_population"].capabilities,
        unused_steps_tolerated=False,
    )
    plan = known_good("capitals_population")
    plan["steps"].append(_geocode("idle", "Capital_From", "Country_From"))
    result = check_route(
        EnrichmentPlan.model_validate(plan), strict, columns("capitals_population")
    )
    assert result.blocking_codes == (C.UNUSED_STEP,)


def test_height_read_from_a_place_record_is_not_the_elevation_of_a_resolved_mountain() -> None:
    plan: Plan = {
        "version": "1.0",
        "steps": [_geocode("place", "Mountain", "Country")],
        "outputs": [
            {
                "step_id": "place",
                "value_field": "elevation",
                "target_column": "height",
                "expected_unit": "m",
            }
        ],
        "assumptions": [],
    }
    result = verdict("mountains", plan)
    assert set(result.blocking_codes) == {C.WRONG_CAPABILITY, C.UNEXPECTED_CAPABILITY}
    messages = [d.message for d in result.deviations]
    assert "height is produced by geocode.elevation, expected lookup_property.value" in messages


def test_the_right_value_in_the_wrong_column_is_a_wrong_field_or_capability() -> None:
    plan = known_good("capitals_coordinates")
    output(plan, "latitude")["value_field"] = "longitude"
    output(plan, "longitude")["value_field"] = "latitude"
    result = verdict("capitals_coordinates", plan)
    assert [d.code for d in result.deviations] == [C.WRONG_FIELD, C.WRONG_FIELD]
    assert result.deviations[0].message == (
        "latitude is read from geocode.longitude, expected geocode.latitude"
    )

    roads = known_good("capitals_roads")
    output(roads, "distance")["step_id"], output(roads, "road")["step_id"] = "road", "distance"
    assert blocking("capitals_roads", roads) == {C.WRONG_CAPABILITY}


def test_a_constant_entity_name_is_reported_without_its_value() -> None:
    plan = known_good("mountains")
    step(plan, "resolve")["inputs"]["name"] = {"kind": "literal", "value": "fake_age_entity"}
    result = verdict("mountains", plan)
    assert [d.code for d in result.deviations] == [C.LITERAL_INPUT]
    message = result.deviations[0].message
    assert "step resolve" in message and "name" in message
    assert "fake_age_entity" not in message


def test_a_wrong_property_parameter_is_a_wrong_parameter() -> None:
    plan = known_good("mountains")
    step(plan, "elevation")["parameters"]["property"] = "population"
    result = verdict("mountains", plan)
    assert [d.code for d in result.deviations] == [C.WRONG_PARAMETER]
    assert "property='population', expected 'elevation'" in result.deviations[0].message


def test_a_missing_or_wrong_entity_type_or_unit_is_a_wrong_parameter() -> None:
    plan = known_good("mountains")
    step(plan, "resolve")["parameters"] = {}
    assert blocking("mountains", plan) == {C.WRONG_PARAMETER}
    plan = known_good("capitals")
    step(plan, "distance")["parameters"]["unit"] = "mi"
    assert blocking("capitals", plan) == {C.WRONG_PARAMETER}


@pytest.mark.parametrize("case", ["capitals", "capitals_roads"])
def test_a_capital_role_restriction_is_a_deviation_where_the_instruction_says_literal_cities(
    case: str,
) -> None:
    plan = known_good(case)
    step(plan, "from_city")["parameters"]["required_role"] = "country_capital"
    assert blocking(case, plan) == {C.WRONG_PARAMETER}


@pytest.mark.parametrize("case", ["capitals_coordinates", "capitals_population"])
def test_the_capital_role_is_open_where_the_instruction_only_says_the_city(case: str) -> None:
    plan = known_good(case)
    assert verdict(case, plan).deviations == ()  # absent: nothing to report

    step(plan, "place")["parameters"]["required_role"] = "country_capital"
    result = verdict(case, plan)
    assert result.on_route
    assert [(d.code, d.blocking) for d in result.deviations] == [(C.OPTIONAL_PARAMETER_SET, False)]
    assert result.deviations[0].message == (
        "step place sets required_role, which the task leaves open"
    )
    assert result.note_codes == (C.OPTIONAL_PARAMETER_SET,)

    step(plan, "place")["parameters"]["required_role"] = "capital_of_region"
    assert blocking(case, plan) == {C.WRONG_PARAMETER}
    step(plan, "place")["parameters"]["required_role"] = None
    assert blocking(case, plan) == {C.WRONG_PARAMETER}


def test_a_wrong_source_column_names_the_step_and_the_headers() -> None:
    plan = known_good("capitals")
    step(plan, "from_city")["inputs"]["country"]["name"] = "Country_To"
    result = verdict("capitals", plan)
    assert [d.code for d in result.deviations] == [C.WRONG_SOURCE_COLUMN]
    assert result.deviations[0].message == (
        "step from_city reads input country from column Country_To, expected Country_From"
    )


def test_a_source_of_another_kind_is_a_wrong_upstream() -> None:
    plan = known_good("capitals")
    step(plan, "distance")["inputs"]["point_a"] = {"kind": "column", "name": "Capital_From"}
    assert blocking("capitals", plan) == {C.WRONG_UPSTREAM}

    plan = known_good("capitals")
    step(plan, "from_city")["inputs"]["city"] = {"kind": "step_output", "step_id": "to_city"}
    assert blocking("capitals", plan) == {C.WRONG_UPSTREAM}

    # The ratio the wrong way round: road over road distance is not the detour.
    roads = known_good("capitals_roads")
    inputs = step(roads, "detour")["inputs"]
    inputs["numerator"], inputs["denominator"] = inputs["denominator"], inputs["numerator"]
    assert blocking("capitals_roads", roads) == {C.WRONG_UPSTREAM}


def test_inputs_the_route_does_not_use_or_lacks_are_reported() -> None:
    plan = known_good("capitals_population")
    step(plan, "place")["inputs"]["administrative_area"] = {"kind": "column", "name": "Capital_To"}
    assert blocking("capitals_population", plan) == {C.UNEXPECTED_INPUT}
    plan = known_good("capitals_population")
    del step(plan, "place")["inputs"]["country"]
    assert blocking("capitals_population", plan) == {C.MISSING_INPUT}


def test_an_upstream_deviation_is_found_through_a_chain_and_reported_once() -> None:
    plan = known_good("capitals_roads")
    step(plan, "from_city")["inputs"]["city"]["name"] = "Capital_To"
    result = verdict("capitals_roads", plan)
    # distance, road and detour all pass through from_city; the sentence appears once.
    assert [d.code for d in result.deviations] == [C.WRONG_SOURCE_COLUMN]


def test_a_dangling_or_circular_reference_does_not_break_the_check() -> None:
    plan = known_good("capitals")
    output(plan, "distance")["step_id"] = "nowhere"
    assert blocking("capitals", plan) == {C.WRONG_UPSTREAM}
    plan = known_good("capitals")
    step(plan, "from_city")["inputs"]["city"] = {"kind": "step_output", "step_id": "distance"}
    assert C.WRONG_UPSTREAM in blocking("capitals", plan)


def test_no_message_carries_a_table_value() -> None:
    plan = known_good("mountains")
    step(plan, "resolve")["inputs"]["name"] = {"kind": "literal", "value": 4242.5}
    step(plan, "resolve")["inputs"]["country"] = {"kind": "literal", "value": "Nepal"}
    text = " ".join(d.message for d in verdict("mountains", plan).deviations)
    assert "Nepal" not in text and "4242" not in text


# --- targets the system has no source for ---------------------------------------------------


def test_the_two_partial_cases_expect_the_other_target_to_be_handed_away() -> None:
    assert set(PARTIAL_CASES) == {case for case in INSTRUCTIONS if ROUTES[case].unfilled}
    for case, (base, target) in PARTIAL_CASES.items():
        assert ROUTES[case].unfilled == (target,)
        # The target that has a source is routed exactly as in the plain case.
        assert ROUTES[case].targets == ROUTES[base].targets
        assert ROUTES[case].capabilities == ROUTES[base].capabilities
        assert INSTRUCTIONS[case] != INSTRUCTIONS[base]
        assert f"into a new column named {target}" in INSTRUCTIONS[case]
    assert all(not ROUTES[case].unfilled for case in CASES) and len(CASES) == 6


@pytest.mark.parametrize("case", PARTIAL_CASES)
def test_a_plan_for_the_target_with_a_source_that_hands_the_other_away_is_on_the_route(
    case: str,
) -> None:
    base, target = PARTIAL_CASES[case]
    result = check_route(
        EnrichmentPlan.model_validate(known_good(base)), ROUTES[case], columns(case), [target]
    )
    assert result.deviations == () and result.on_route


@pytest.mark.parametrize("case", PARTIAL_CASES)
def test_a_plan_that_hands_away_the_target_that_has_a_source_is_blocked(case: str) -> None:
    base, _ = PARTIAL_CASES[case]
    wanted = ROUTES[case].targets[0].target
    plan = known_good(base)
    plan["outputs"] = []
    # Nothing is written, and both the target with a source and the other are handed away.
    # (A real plan like this is refused by the model rule; the check still judges it.)
    result = check_route(
        EnrichmentPlan.model_validate(plan),
        ROUTES[case],
        columns(case),
        [wanted, PARTIAL_CASES[case][1]],
    )
    assert set(result.blocking_codes) == {C.TARGET_HANDED_AWAY}
    assert wanted in result.deviations[0].message and not result.on_route


@pytest.mark.parametrize("case", PARTIAL_CASES)
def test_a_plan_that_writes_the_target_nothing_can_fill_is_not_handing_it_away(case: str) -> None:
    base, target = PARTIAL_CASES[case]
    plan = known_good(base)
    plan["outputs"].append(
        {"step_id": plan["outputs"][0]["step_id"], "value_field": "value", "target_column": target}
    )
    result = check_route(EnrichmentPlan.model_validate(plan), ROUTES[case], columns(case), [])
    assert set(result.blocking_codes) == {C.TARGET_NOT_HANDED_AWAY}  # not also EXTRA_TARGET
    assert target in result.deviations[0].message and not result.on_route


@pytest.mark.parametrize("case", PARTIAL_CASES)
def test_a_plan_that_neither_writes_nor_lists_the_target_is_not_on_the_route(case: str) -> None:
    base, _ = PARTIAL_CASES[case]
    result = check_route(
        EnrichmentPlan.model_validate(known_good(base)), ROUTES[case], columns(case), []
    )
    assert set(result.blocking_codes) == {C.TARGET_NOT_HANDED_AWAY}


@pytest.mark.parametrize("case", PARTIAL_CASES)
def test_a_refusal_has_no_plan_to_judge(case: str) -> None:
    from scripts.experiment_planner import judge_route

    record: dict[str, Any] = {"status": "unsupported", "valid": False, "plan": None}
    judge_route(record, case)
    assert record["route"] == {"verdict": "not_judged", "deviations": []}


@pytest.mark.parametrize("case", CASES)
def test_listing_a_target_that_has_a_source_is_handing_it_away(case: str) -> None:
    for target in (route.target for route in ROUTES[case].targets):
        result = check_route(
            EnrichmentPlan.model_validate(known_good(case)), ROUTES[case], columns(case), [target]
        )
        assert C.TARGET_HANDED_AWAY in result.blocking_codes and not result.on_route
        assert any(target in d.message for d in result.deviations)
    # Listing nothing is as before.
    assert verdict(case, known_good(case)).deviations == ()
