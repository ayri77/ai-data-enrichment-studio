"""Unit tests for the OpenAI Responses API plan completion client."""

from unittest.mock import Mock

import pytest

from ai_data_enrichment.planning.openai_client import (
    OpenAICompletionError,
    OpenAIPlanClient,
)


def test_openai_client_requests_json_and_returns_response() -> None:
    """Use JSON mode and include the plan schema in model context."""

    api = Mock()
    api.responses.create.return_value.output_text = '{"version":"1.0"}'
    api.responses.create.return_value.status = "completed"

    client = OpenAIPlanClient(
        api=api,
        model="test-model",
    )

    schema = {
        "type": "object",
        "properties": {
            "version": {"type": "string"},
        },
    }

    result = client.complete(
        messages=[
            {
                "role": "system",
                "content": "Generate a JSON enrichment plan.",
            }
        ],
        response_schema=schema,
    )

    assert result == '{"version":"1.0"}'

    kwargs = api.responses.create.call_args.kwargs

    assert kwargs["model"] == "test-model"
    assert kwargs["text"] == {"format": {"type": "json_object"}}
    assert kwargs["store"] is False

    assert any("version" in message["content"] for message in kwargs["input"])


def test_openai_client_rejects_incomplete_response() -> None:
    """Reject incomplete responses before returning their content."""

    api = Mock()
    api.responses.create.return_value.status = "incomplete"
    api.responses.create.return_value.output_text = '{"version":'

    client = OpenAIPlanClient(
        api=api,
        model="test-model",
    )

    with pytest.raises(OpenAICompletionError, match="incomplete"):
        client.complete(
            messages=[
                {
                    "role": "system",
                    "content": "Generate a JSON enrichment plan.",
                }
            ],
            response_schema={"type": "object"},
        )


@pytest.mark.parametrize(
    "output_text",
    [
        "",
        " ",
        "\t\n",
        None,
    ],
)
def test_openai_client_rejects_empty_response(
    output_text: str | None,
) -> None:
    """Reject completed responses without meaningful generated text."""

    api = Mock()
    api.responses.create.return_value.status = "completed"
    api.responses.create.return_value.output_text = output_text

    client = OpenAIPlanClient(
        api=api,
        model="test-model",
    )

    with pytest.raises(OpenAICompletionError, match="empty"):
        client.complete(
            messages=[
                {
                    "role": "system",
                    "content": "Generate a JSON enrichment plan.",
                }
            ],
            response_schema={"type": "object"},
        )


def test_openai_client_applies_output_token_limit() -> None:
    """Pass the configured output token limit to the Responses API."""

    api = Mock()
    api.responses.create.return_value.status = "completed"
    api.responses.create.return_value.output_text = "{}"

    client = OpenAIPlanClient(
        api=api,
        model="test-model",
        max_output_tokens=2048,
    )

    client.complete(
        messages=[
            {"role": "user", "content": "Generate an enrichment plan."},
        ],
        response_schema={"type": "object"},
    )

    kwargs = api.responses.create.call_args.kwargs
    assert kwargs["max_output_tokens"] == 2048


@pytest.mark.parametrize("max_output_tokens", [0, -1, -100])
def test_openai_client_rejects_invalid_token_limit(
    max_output_tokens: int,
) -> None:
    """Reject non-positive output token limits during initialization."""

    api = Mock()

    with pytest.raises(ValueError, match="max_output_tokens must be positive"):
        OpenAIPlanClient(
            api=api,
            model="test-model",
            max_output_tokens=max_output_tokens,
        )

    api.responses.create.assert_not_called()
