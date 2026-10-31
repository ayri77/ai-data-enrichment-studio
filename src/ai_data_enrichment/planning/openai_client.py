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
    ) -> None:
        """Initialize the client with API configuration and token limit."""

        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")

        self._api = api
        self._model = model
        self._max_output_tokens = max_output_tokens

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> str:
        """Request a JSON response matching the supplied plan schema."""
        schema_prompt = "Return a JSON object matching this JSON Schema:\n" + json.dumps(
            response_schema, ensure_ascii=False
        )

        response = self._api.responses.create(
            model=self._model,
            input=[
                *messages,
                {"role": "user", "content": schema_prompt},
            ],
            text={"format": {"type": "json_object"}},
            store=False,
            max_output_tokens=self._max_output_tokens,
        )

        if response.status != "completed":
            raise OpenAICompletionError(f"OpenAI response not completed: {response.status}")

        output_text = response.output_text

        if not output_text or not output_text.strip():
            raise OpenAICompletionError("OpenAI response contains empty output text")

        return output_text
