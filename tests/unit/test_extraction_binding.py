"""The binding of the text extraction: found by the helpers, hidden from the planner."""

import pytest
from fastapi.testclient import TestClient

from ai_data_enrichment.api import create_app
from ai_data_enrichment.capabilities.bindings import (
    BINDINGS,
    CATEGORIES,
    EXTRACTION_BINDINGS,
    FALLBACK_BINDINGS,
    Trust,
    binding_for,
    find_binding,
)
from ai_data_enrichment.capabilities.definitions import (
    get_builtin_capabilities,
    offered_capabilities,
)
from ai_data_enrichment.evaluation.plan_run_fixtures import offline_executor
from ai_data_enrichment.orchestration.live_executor import LIVE_SOURCES
from ai_data_enrichment.output.provenance import (
    BASIS_WORDS,
    SOURCE_NAMES,
    TRUST_WORDS,
    CellProvenance,
    note_text,
    trust_words,
)
from ai_data_enrichment.plan_run import PlanRunSettings
from ai_data_enrichment.plan_run_worker import (
    builtin_registry,
    offered_categories,
    offered_descriptions,
    remote_sources,
)
from ai_data_enrichment.providers.text_extraction import EXTRACTION_CAPABILITY, EXTRACTION_SOURCE


def test_the_binding_states_the_source_the_trust_and_the_category() -> None:
    assert [item.capability_id for item in EXTRACTION_BINDINGS] == ["text_extraction"]
    found = binding_for("text_extraction")
    assert find_binding("text_extraction") is found is EXTRACTION_BINDINGS[0]
    assert found.source_id == "openai_text_extraction"
    assert found.trust is Trust.EXTRACTED and found.trust.value == "extracted"
    assert found.category == "from_text" and CATEGORIES[-1] == "from_text"
    assert found.reusable and found.requires_key and found.optional
    assert not found.always_bound and found.name_resolution is None
    # The ids the provider module states are the ones the table states.
    assert (found.capability_id, found.source_id) == (EXTRACTION_CAPABILITY, EXTRACTION_SOURCE)


def test_the_other_bindings_are_as_they_were() -> None:
    assert all(item.trust is not Trust.EXTRACTED for item in (*BINDINGS, *FALLBACK_BINDINGS))
    assert [item.capability_id for item in FALLBACK_BINDINGS] == ["web_lookup"]
    assert all(item.category != "from_text" for item in (*BINDINGS, *FALLBACK_BINDINGS))


def test_it_is_not_offered_not_executed_by_default_and_asks_no_remote_source() -> None:
    assert "text_extraction" not in {item.capability_id for item in BINDINGS}
    assert "text_extraction" not in LIVE_SOURCES
    assert "text_extraction" not in {spec.id for spec in offered_capabilities(road_distance=True)}
    assert "text_extraction" not in {spec.id for spec in get_builtin_capabilities()}
    assert builtin_registry().get("text_extraction") is None
    assert "openai_text_extraction" not in remote_sources(builtin_registry())


def test_it_is_in_no_description_and_in_no_category_a_client_is_told() -> None:
    offered = offered_descriptions()
    assert "text_extraction" not in {item["id"] for item in offered}
    assert "from_text" not in offered_categories(offered)
    assert all(item["category"] != "from_text" for item in offered)
    assert all(item["trust"] != "extracted" for item in offered)
    # Nor in what the planner is shown.
    assert "text_extraction" not in str(builtin_registry().describe_all())


def test_the_catalog_route_does_not_list_it_as_an_entry_or_a_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTESERVICE_API_KEY", raising=False)
    with TestClient(
        create_app(settings=PlanRunSettings(), offline_executor_factory=offline_executor)
    ) as client:
        body = client.get("/api/capabilities").json()
        assert body["categories"] == ["lookup", "distance", "calculate"]
        assert "text_extraction" not in client.get("/api/capabilities").text
        assert "from_text" not in client.get("/api/capabilities").text
        assert "extracted" not in {item["trust"] for item in body["capabilities"]}


def test_the_workbook_says_in_words_what_the_kind_of_trust_and_the_basis_mean() -> None:
    assert Trust.EXTRACTED in TRUST_WORDS and "openai_text_extraction" in SOURCE_NAMES
    assert {"literal", "derived"} <= set(BASIS_WORDS)
    entry = CellProvenance(
        cell="E2",
        column="Amount",
        value=9.99,
        capability="text_extraction",
        source="openai_text_extraction",
        reference="",
        trust="extracted",
        basis="derived",
        quote="Total: 9,99 EUR",
    )
    note = note_text(entry)
    assert "Source: OpenAI text extraction" in note
    assert "Reference" not in note and "Quote: Total: 9,99 EUR" in note
    assert f"Kind of trust: {trust_words('extracted')}" in note
    assert "Basis: derived from the quoted passage, not stated in it as it is" in note
