"""Test-only OpenAI HTTP responses behind the actual trusted loopback launcher."""

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import httpx
from openai import OpenAI

from ai_data_enrichment.local_server import main


def respond(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert request.url == "https://api.openai.com/v1/responses"
    context = json.loads(body["input"][1]["content"])
    columns = {column["name"] for column in context["dataset_profile"]["columns"]}
    name = "capitals" if "distance" in columns else "mountains"
    plan = json.loads(Path(f"demo/{name}_api_plan.json").read_text("utf-8"))
    return httpx.Response(
        200,
        json={
            "id": "response-test",
            "object": "response",
            "created_at": 0,
            "model": body["model"],
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "id": "msg-test",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps({"status": "ready", "plan": plan, "reason": None}),
                            "annotations": [],
                        }
                    ],
                }
            ],
        },
    )


def fixture_openai(**kwargs):
    return OpenAI(
        **kwargs, http_client=cast(Any, httpx.Client(transport=httpx.MockTransport(respond)))
    )


if __name__ == "__main__":
    with patch("ai_data_enrichment.plan_run.OpenAI", fixture_openai):
        main()
