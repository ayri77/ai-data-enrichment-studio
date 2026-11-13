"""One catalog describes every model the application can use."""

from types import SimpleNamespace

from ai_data_enrichment.planning.model_catalog import (
    DEFAULT_MODEL,
    MODELS,
    SUPPORTED_MODELS,
    model_client,
    model_spec,
)
from ai_data_enrichment.provider_settings import ConfigurationRequest, ProviderConfiguration


class Api:
    """Records the one request a client sends."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(status="completed", output_text="{}")


def sent(model_id: str, answer_tokens: int = 1000) -> dict:
    api = Api()
    model_client(api=api, model_id=model_id, answer_tokens=answer_tokens).complete(
        messages=[], response_schema={}
    )
    [request] = api.requests
    return request


def test_the_default_is_a_measured_model_of_the_catalog() -> None:
    assert len(set(SUPPORTED_MODELS)) == len(MODELS)
    assert model_spec(DEFAULT_MODEL).measured


def test_a_model_without_reasoning_gets_the_answer_budget_and_no_effort() -> None:
    request = sent("gpt-4.1-mini")
    assert request["model"] == "gpt-4.1-mini"
    assert request["max_output_tokens"] == 1000
    assert "reasoning" not in request


def test_a_reasoning_model_gets_its_effort_and_room_for_reasoning() -> None:
    spec = model_spec("gpt-6-luna")
    assert spec.reasoning_effort and spec.reasoning_reserve_tokens > 0 and not spec.measured
    request = sent("gpt-6-luna")
    assert request["reasoning"] == {"effort": spec.reasoning_effort}
    # The answer keeps its budget; reasoning has its own on top.
    assert request["max_output_tokens"] == 1000 + spec.reasoning_reserve_tokens


def test_a_model_outside_the_catalog_gets_plain_defaults() -> None:
    request = sent("some-other-model")
    assert request["model"] == "some-other-model" and "reasoning" not in request


def test_the_workspace_offers_and_accepts_exactly_the_catalog() -> None:
    provider = ProviderConfiguration.from_environment(None)
    assert provider.model == DEFAULT_MODEL
    listed = provider.readiness(network=True, offline=False, configurable=True)["supported_models"]
    assert [(model["id"], model["measured"]) for model in listed] == [
        (model.id, model.measured) for model in MODELS
    ]
    provider.apply(ConfigurationRequest(model="gpt-6-luna"))
    assert provider.model == "gpt-6-luna"
    assert ProviderConfiguration.from_environment("gpt-6-luna").model == "gpt-6-luna"
    # A model outside the catalog is reported as unset, not silently replaced.
    assert ProviderConfiguration.from_environment("gpt-4o-mini").model is None
