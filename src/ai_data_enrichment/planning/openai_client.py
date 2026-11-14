"""OpenAI Responses API adapter for enrichment plan generation."""

import json
from typing import Any


class OpenAICompletionError(RuntimeError):
    """Raised when the OpenAI API does not complete a response."""


class OpenAIPlanClient:
    """Generate JSON plans using the OpenAI Responses API."""

    def __init__(
        self,
        *,
        api: Any,
        model: str,
        max_output_tokens: int = 2048,
        reasoning_effort: str | None = None,
    ) -> None:
        """Initialize the client with API configuration and token limit.

        ``reasoning_effort`` is for reasoning models only and is sent only when
        set: a model without reasoning rejects the parameter. For a reasoning
        model the hidden reasoning tokens count against ``max_output_tokens``,
        so a budget that fits a plan may not fit the reasoning before it.
        """

        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if reasoning_effort is not None and not reasoning_effort.strip():
            raise ValueError("reasoning_effort must not be empty")

        self._api = api
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort
        # Tokens the provider reported for the requests of this client, hidden
        # reasoning included; counted even when the answer was not usable.
        self.input_tokens = 0
        self.output_tokens = 0

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> str:
        """Request a JSON response matching the supplied plan schema."""
        # JSON mode requests well-formed output; Pydantic validates the schema later.
        schema_prompt = "Return a JSON object matching this JSON Schema:\n" + json.dumps(
            response_schema, ensure_ascii=False
        )

        # Do not store planning responses at the provider; execution is not part of this call.
        response = self._api.responses.create(
            model=self._model,
            input=[
                *messages,
                {"role": "user", "content": schema_prompt},
            ],
            text={"format": {"type": "json_object"}},
            store=False,
            max_output_tokens=self._max_output_tokens,
            **(
                {"reasoning": {"effort": self._reasoning_effort}}
                if self._reasoning_effort is not None
                else {}
            ),
        )

        usage = getattr(response, "usage", None)
        for name in ("input_tokens", "output_tokens"):
            reported = getattr(usage, name, None)
            if isinstance(reported, int) and not isinstance(reported, bool) and reported > 0:
                setattr(self, name, getattr(self, name) + reported)

        # Truncated/incomplete responses are failures, not partially usable plans.
        if response.status != "completed":
            raise OpenAICompletionError(f"OpenAI response not completed: {response.status}")

        output_text = response.output_text

        if not output_text or not output_text.strip():
            raise OpenAICompletionError("OpenAI response contains empty output text")

        # Return raw text so the planner can parse and validate it independently.
        return output_text
