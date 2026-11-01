"""Offline integration tests for Wikidata mountain enrichment."""

from collections.abc import Mapping

from ai_data_enrichment.capabilities.definitions import (
    get_lookup_property_spec,
    get_resolve_entity_spec,
)
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.executor import DAGExecutor
from ai_data_enrichment.execution.models import (
    EntityRef,
    Quantity,
    RowExecutionResult,
)
from ai_data_enrichment.providers.registry import ProviderRegistry
from ai_data_enrichment.providers.wikidata import (
    WIKIDATA_API_URL,
    WikidataElevationHandler,
    WikidataMountainResolver,
)
from ai_data_enrichment.validation.plan_validator import PlanValidator


def entity_statement(qid: str) -> dict[str, object]:
    """Build a Wikidata entity-reference statement."""
    return {
        "rank": "normal",
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {
                "type": "wikibase-entityid",
                "value": {"id": qid},
            },
        },
    }


class WikidataFixtureTransport:
    """Simulate Wikidata API responses without network access."""

    def __init__(self, *, elevation_available: bool = True) -> None:
        self.elevation_available = elevation_available
        self.requests: list[dict[str, str]] = []

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        assert url == WIKIDATA_API_URL
        assert timeout_seconds == 10.0

        self.requests.append(dict(params))

        if params["action"] == "wbsearchentities":
            if params["search"] == "Unknown Mountain":
                return {"search": []}

            assert params["search"] == "Mount Everest"

            return {
                "search": [
                    {"id": "Q513", "label": "Mount Everest"},
                ],
            }

        assert params["action"] == "wbgetentities"

        if params["ids"] == "Q513":
            claims: dict[str, object] = {
                "P31": [entity_statement("Q8502")],
                "P17": [entity_statement("Q837")],
            }

            if self.elevation_available:
                claims["P2044"] = [
                    {
                        "rank": "normal",
                        "mainsnak": {
                            "snaktype": "value",
                            "datavalue": {
                                "type": "quantity",
                                "value": {
                                    "amount": "+8848.86",
                                    "unit": ("http://www.wikidata.org/entity/Q11573"),
                                },
                            },
                        },
                    },
                ]

            return {
                "entities": {
                    "Q513": {
                        "id": "Q513",
                        "claims": claims,
                    },
                },
            }

        if params["ids"] == "Q837":
            return {
                "entities": {
                    "Q837": {
                        "id": "Q837",
                        "claims": {
                            "P297": [
                                {
                                    "rank": "normal",
                                    "mainsnak": {
                                        "snaktype": "value",
                                        "datavalue": {
                                            "type": "string",
                                            "value": "NP",
                                        },
                                    },
                                },
                            ],
                        },
                    },
                },
            }

        raise AssertionError(f"Unexpected Wikidata entity: {params['ids']}")


def execute_mountain_dag(
    transport: WikidataFixtureTransport,
    *,
    mountain: str,
) -> RowExecutionResult:
    """Validate and execute the mountain enrichment DAG."""

    capabilities = CapabilityRegistry()
    capabilities.register(get_resolve_entity_spec())
    capabilities.register(get_lookup_property_spec())

    # The dependent step is deliberately listed first.
    plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="lookup",
                capability="lookup_property",
                inputs={
                    "entity": StepOutputReference(
                        kind="step_output",
                        step_id="resolve",
                    ),
                },
                parameters={"property": "elevation"},
            ),
            PlanStep(
                id="resolve",
                capability="resolve_entity",
                inputs={
                    "name": ColumnReference(
                        kind="column",
                        name="mountain",
                    ),
                    "country": ColumnReference(
                        kind="column",
                        name="country",
                    ),
                },
                parameters={"entity_type": "mountain"},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="lookup",
                value_field="value",
                target_column="elevation_m",
            ),
        ],
        assumptions=[],
    )

    profile = DatasetProfile(
        dataset_id="mountains-fixture",
        name="Mountains integration fixture",
        columns=[
            ColumnProfile(
                name="mountain",
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
            ),
            ColumnProfile(
                name="country",
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
            ),
        ],
    )

    validation = PlanValidator(
        registry=capabilities,
        dataset_profile=profile,
    ).validate(plan)

    assert validation.valid, validation.errors

    providers = ProviderRegistry(capabilities)
    providers.register(
        capability_id="resolve_entity",
        provider_id="wikidata",
        handler=WikidataMountainResolver(transport=transport),
    )
    providers.register(
        capability_id="lookup_property",
        provider_id="wikidata",
        handler=WikidataElevationHandler(transport=transport),
    )

    handlers = providers.resolve_handlers(
        capability_ids=[step.capability for step in plan.steps],
        selected_providers={
            "resolve_entity": "wikidata",
            "lookup_property": "wikidata",
        },
    )

    row = {
        "mountain": mountain,
        "country": "Nepal",
    }
    original_row = dict(row)

    execution = DAGExecutor(handlers).execute_row(plan, row)

    # Execution must not modify source data.
    assert row == original_row

    # Dependency order must not depend on plan step ordering.
    assert [step.step_id for step in execution.steps] == [
        "resolve",
        "lookup",
    ]

    return execution


def test_mountain_resolution_and_elevation_dag() -> None:
    """Resolve a mountain and retrieve its typed elevation."""

    transport = WikidataFixtureTransport()
    execution = execute_mountain_dag(
        transport,
        mountain="Mount Everest",
    )

    outcomes = {step.step_id: step.result for step in execution.steps}

    resolved = outcomes["resolve"]
    elevation = outcomes["lookup"]

    assert resolved.status == "success"
    assert resolved.value == EntityRef(
        source="wikidata",
        entity_id="Q513",
    )

    assert elevation.status == "success"
    assert elevation.value == Quantity(
        value=8848.86,
        unit="m",
    )

    assert resolved.evidence
    assert elevation.evidence

    assert all(item.source == "wikidata" for item in (*resolved.evidence, *elevation.evidence))

    assert "P31" in resolved.evidence[0].reference
    assert "P297" in resolved.evidence[0].reference
    assert elevation.evidence[0].reference == "Q513:P2044"

    assert [(request["action"], request.get("ids")) for request in transport.requests] == [
        ("wbsearchentities", None),
        ("wbgetentities", "Q513"),
        ("wbgetentities", "Q837"),
        ("wbgetentities", "Q513"),
    ]


def test_missing_mountain_blocks_elevation_lookup() -> None:
    """Do not execute property lookup after resolution fails."""

    transport = WikidataFixtureTransport()
    execution = execute_mountain_dag(
        transport,
        mountain="Unknown Mountain",
    )

    outcomes = {step.step_id: step.result for step in execution.steps}

    assert outcomes["resolve"].status == "failure"
    assert outcomes["resolve"].error_code == "NOT_FOUND"

    assert outcomes["lookup"].status == "failure"
    assert outcomes["lookup"].error_code == "DEPENDENCY_FAILED"
    assert outcomes["lookup"].value is None
    assert not outcomes["lookup"].evidence

    # The failed resolution must not trigger any entity lookup.
    assert len(transport.requests) == 1
    assert transport.requests[0]["action"] == "wbsearchentities"


def test_missing_elevation_preserves_resolved_identity() -> None:
    """Keep successful entity resolution when elevation is absent."""

    transport = WikidataFixtureTransport(
        elevation_available=False,
    )
    execution = execute_mountain_dag(
        transport,
        mountain="Mount Everest",
    )

    outcomes = {step.step_id: step.result for step in execution.steps}

    assert outcomes["resolve"].status == "success"
    assert outcomes["resolve"].value == EntityRef(
        source="wikidata",
        entity_id="Q513",
    )
    assert outcomes["resolve"].evidence

    assert outcomes["lookup"].status == "failure"
    assert outcomes["lookup"].error_code == "NOT_FOUND"
    assert outcomes["lookup"].value is None
    assert not outcomes["lookup"].evidence

    assert len(transport.requests) == 4
