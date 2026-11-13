"""The language models the application can use, described in one place.

Everything that depends on the choice of model reads it from here: the list a
user can choose from, the default, and the request parameters a model needs.
Adding a model is one entry in ``MODELS``.

``measured`` records whether planning reliability was measured on both samples
(docs/26-live-planning-reliability.md). An unmeasured model can be chosen, and
the interface says that it was not measured. The default is always a measured one.
"""

from dataclasses import dataclass
from typing import Any

from ai_data_enrichment.planning.openai_client import OpenAIPlanClient


@dataclass(frozen=True)
class ModelSpec:
    id: str
    measured: bool
    # Reasoning models only. Sent with every request; a model without reasoning
    # rejects the parameter, so it stays None for those.
    reasoning_effort: str | None = None
    # Hidden reasoning tokens count against a request's output limit. This is
    # added to what the visible answer needs, so reasoning does not crowd it out.
    reasoning_reserve_tokens: int = 0


MODELS: tuple[ModelSpec, ...] = (
    # Measured 2026-11-13 with effort "low": 10 of 10 valid plans on each sample,
    # all on the first attempt (docs/26). Other efforts were not measured, and
    # the reserve is headroom: the measurement passed without it.
    # gpt-4o-mini was removed after measurement (1 valid Mountains plan in 20);
    # see docs/26 before trusting a new entry.
    ModelSpec("gpt-6-luna", measured=True, reasoning_effort="low", reasoning_reserve_tokens=4096),
    ModelSpec("gpt-4.1-mini", measured=True),
    ModelSpec("gpt-4.1", measured=False),
)
# Both measured models met the same bar; this one is the cheaper by list price.
DEFAULT_MODEL = "gpt-6-luna"
SUPPORTED_MODELS: tuple[str, ...] = tuple(model.id for model in MODELS)
_BY_ID = {model.id: model for model in MODELS}


def model_spec(model_id: str) -> ModelSpec:
    """The catalog entry, or plain defaults for a model named outside the catalog.

    Only the command line accepts such a model; the web workspace offers and
    accepts catalog entries alone.
    """
    return _BY_ID.get(model_id, ModelSpec(model_id, measured=False))


def model_client(*, api: Any, model_id: str, answer_tokens: int) -> OpenAIPlanClient:
    """A client for one request whose visible answer needs ``answer_tokens``."""
    spec = model_spec(model_id)
    return OpenAIPlanClient(
        api=api,
        model=spec.id,
        max_output_tokens=answer_tokens + spec.reasoning_reserve_tokens,
        reasoning_effort=spec.reasoning_effort,
    )


assert _BY_ID[DEFAULT_MODEL].measured, "The default model must be a measured one."
