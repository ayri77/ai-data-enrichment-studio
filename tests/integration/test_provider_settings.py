"""Local configuration boundary and real live-planner adapter, with no external I/O."""

import json
import logging
from secrets import token_urlsafe
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import OpenAI
from tests.integration.test_plan_run_api import approve, request_body, start, upload, wait

from ai_data_enrichment import plan_run
from ai_data_enrichment.api import create_app
from ai_data_enrichment.evaluation.plan_run_fixtures import offline_executor
from ai_data_enrichment.plan_run import PlanRunSettings

URL = "http://127.0.0.1:8010"
HEADERS = {"Origin": URL, "X-Enrichment-Config": "1"}
MODEL = "gpt-4.1-mini"


def local_client(**kwargs):
    return TestClient(
        create_app(local_browser_configuration=True, **kwargs),
        base_url=URL,
        client=("127.0.0.1", 50000),
    )


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("ENRICHMENT_ALLOW_NETWORK", raising=False)


def apply(client, key=None, model: str | None = MODEL):
    return client.put(
        "/api/provider-settings",
        headers=HEADERS,
        json={"model": model, **({"api_key": key} if key else {})},
    )


def test_startup_replace_remove_restart_and_no_side_effects(monkeypatch, caplog):
    old, new = token_urlsafe(32), token_urlsafe(32)
    monkeypatch.setenv("OPENAI_API_KEY", old)
    monkeypatch.setenv("OPENAI_MODEL", MODEL)
    monkeypatch.setattr(plan_run, "OpenAI", lambda **kw: pytest.fail("Saving contacted OpenAI"))
    with local_client() as client:
        response = client.get("/api/provider-settings")
        assert response.headers["cache-control"] == "no-store"
        status = response.json()
        assert status["key_present"] and status["model"] == MODEL
        assert not status["server_network_allowed"] and not status["live_ready"]
        replaced = apply(client, new)
        assert replaced.status_code == 200
        assert apply(client, model="gpt-4.1").json()["model"] == "gpt-4.1"
        assert apply(client, model=None).json()["model"] is None
        removed = client.delete("/api/provider-settings/key", headers=HEADERS)
        assert not removed.json()["key_present"]
        assert not apply(client).json()["key_present"]  # No fallback to the environment key.
        assert old not in response.text + replaced.text + removed.text + caplog.text
        assert new not in response.text + replaced.text + removed.text + caplog.text
    # Each new app snapshots startup defaults, never another app's runtime configuration.
    with local_client() as client:
        assert client.get("/api/provider-settings").json()["model"] == MODEL
    monkeypatch.delenv("OPENAI_API_KEY")
    with local_client() as client:
        assert not client.get("/api/provider-settings").json()["key_present"]


@pytest.mark.parametrize(
    "headers,base_url,peer",
    [
        ({}, URL, "127.0.0.1"),
        ({**HEADERS, "Origin": "null"}, URL, "127.0.0.1"),
        ({**HEADERS, "Origin": "https://evil.example"}, URL, "127.0.0.1"),
        ({**HEADERS, "Origin": "http://localhost:8010"}, URL, "127.0.0.1"),
        (HEADERS, "http://evil.example:8010", "127.0.0.1"),
        (HEADERS, "http://192.168.1.10:8010", "127.0.0.1"),
        (HEADERS, "http://0.0.0.0:8010", "127.0.0.1"),
        (HEADERS, URL, "192.168.1.10"),
        ({**HEADERS, "Sec-Fetch-Site": "cross-site"}, URL, "127.0.0.1"),
        ({**HEADERS, "Sec-Fetch-Site": "same-site"}, URL, "127.0.0.1"),
        ({**HEADERS, "X-Forwarded-For": "127.0.0.1"}, URL, "127.0.0.1"),
        ({**HEADERS, "Forwarded": "host=localhost"}, URL, "127.0.0.1"),
        ({"Origin": URL}, URL, "127.0.0.1"),
        ({**HEADERS, "Origin": "http://127.0.0.1:5174"}, URL, "127.0.0.1"),
    ],
)
def test_rejects_untrusted_configuration_before_body_parsing(headers, base_url, peer):
    with TestClient(
        create_app(local_browser_configuration=True), base_url=base_url, client=(peer, 12345)
    ) as client:
        for method, route in [("PUT", ""), ("DELETE", "/key")]:
            response = client.request(
                method, f"/api/provider-settings{route}", headers=headers, content=b"invalid JSON"
            )
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "configuration_forbidden"
        assert not client.get("/api/provider-settings").json()["key_present"]


def test_generic_deployment_disables_mutation_and_does_not_enable_cors():
    with TestClient(create_app(), base_url=URL, client=("127.0.0.1", 123)) as client:
        assert not client.get("/api/provider-settings").json()["configuration_enabled"]
        assert apply(client, token_urlsafe(32)).status_code == 403
        assert client.delete("/api/provider-settings/key", headers=HEADERS).status_code == 403
        preflight = client.options(
            "/api/provider-settings",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "PUT",
            },
        )
        assert "access-control-allow-origin" not in preflight.headers


@pytest.mark.parametrize("origin", ["http://127.0.0.1:5173", "http://localhost:5173"])
def test_vite_proxy_preserved_host_and_origin(origin):
    with TestClient(
        create_app(local_browser_configuration=True), base_url=URL, client=("127.0.0.1", 123)
    ) as client:
        response = client.put(
            "/api/provider-settings",
            headers={
                "Host": origin.removeprefix("http://"),
                "Origin": origin,
                "X-Enrichment-Config": "1",
            },
            json={"model": MODEL, "api_key": token_urlsafe(32)},
        )
        assert response.status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"model": "unsupported-secret-marker"},
        {"model": MODEL, "api_key": "contains space"},
        {"model": MODEL, "api_key": ""},
        {"model": MODEL, "api_key": 123},
        {"model": MODEL, "api_key": "x" * 513},
        {"model": MODEL, "unexpected": "secret-marker"},
    ],
)
def test_validation_never_echoes_input(body, caplog):
    with local_client() as client:
        response = client.put("/api/provider-settings", headers=HEADERS, json=body)
        assert response.status_code == 422
        assert "secret-marker" not in response.text + caplog.text
        assert not client.get("/api/provider-settings").json()["key_present"]
        response = client.put("/api/provider-settings", headers=HEADERS, content=b"x" * 2049)
        assert response.status_code == 415  # Simple/form requests cannot mutate configuration.
        response = client.put(
            "/api/provider-settings",
            headers={**HEADERS, "Content-Type": "application/json"},
            content=b"x" * 2049,
        )
        assert response.status_code == 413


@pytest.mark.parametrize(
    "network,model,key,consent,code",
    [
        (False, MODEL, True, True, "network_disabled"),
        (True, MODEL, True, False, "network_disabled"),
        (True, None, True, True, "llm_not_configured"),
        (True, MODEL, False, True, "missing_api_key"),
    ],
)
def test_readiness_and_authoritative_plan_gates(network, model, key, consent, code, monkeypatch):
    monkeypatch.setattr(plan_run, "OpenAI", lambda **kw: pytest.fail("Gate allowed model call"))
    with local_client(settings=PlanRunSettings(allow_network=network)) as client:
        apply(client, token_urlsafe(32) if key else None, model)
        status = client.get("/api/provider-settings").json()
        assert status["live_ready"] == bool(network and model and key)
        body = request_body(upload(client), generate=True)
        response = client.post("/api/plans", json={**body, "allow_network": consent})
        assert response.json()["error"]["code"] == code
        assert client.post("/api/plans", json=request_body(body["dataset_id"])).status_code == 201


def test_injected_offline_mode_is_explicit():
    with local_client(offline_plan_client=SimpleNamespace()) as client:
        status = client.get("/api/provider-settings").json()
        assert status["planning_mode"] == "offline_injected" and not status["live_ready"]


def test_configured_live_sdk_plan_validation_approval_report_and_no_secret_leak(
    monkeypatch, caplog
):
    secret = token_urlsafe(40)
    calls = []
    from tests.integration.test_plan_run_api import plan_payload

    def respond(request):
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == f"Bearer {secret}"
        body = json.loads(request.content)
        assert body["model"] == MODEL and body["store"] is False
        assert secret not in request.content.decode()
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "id": "response-test",
                "object": "response",
                "created_at": 0,
                "model": MODEL,
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
                                "text": json.dumps(
                                    {"status": "ready", "plan": plan_payload(), "reason": None}
                                ),
                                "annotations": [],
                            }
                        ],
                    }
                ],
            },
        )

    def sdk(**kwargs):
        return OpenAI(
            **kwargs, http_client=cast(Any, httpx.Client(transport=httpx.MockTransport(respond)))
        )

    monkeypatch.setattr(plan_run, "OpenAI", sdk)
    caplog.set_level(logging.DEBUG)
    with local_client(
        settings=PlanRunSettings(allow_network=True), offline_executor_factory=offline_executor
    ) as client:
        configured = apply(client, secret)
        assert configured.json()["live_ready"]
        assert configured.json()["planning_mode"] == "live"
        assert not calls
        body = {**request_body(upload(client), generate=True), "allow_network": True}
        response = client.post("/api/plans", json=body)
        assert response.status_code == 201, response.text
        draft = response.json()
        assert draft["validation"]["valid"] and draft["status"] == "draft"
        assert len(calls) == 1
        assert client.post(f"/api/plans/{draft['plan_id']}/runs", json={}).status_code == 409
        approved = approve(client, draft)
        # Configuration updates neither approve nor invalidate/rewrite an existing exact plan.
        apply(client, secret, "gpt-4.1")
        assert client.get(f"/api/plans/{draft['plan_id']}").json() == approved
        job = wait(client, start(client, approved))
        report = client.get(job["artifacts"]["report"])
        workbook = client.get(job["artifacts"]["result"])
        assert secret not in configured.text + response.text + report.text + caplog.text
        assert secret.encode() not in workbook.content
        directory = cast(FastAPI, client.app).state.directory
        assert all(
            secret.encode() not in f.read_bytes() for f in directory.rglob("*") if f.is_file()
        )


def test_provider_failure_is_sanitized(monkeypatch, caplog):
    secret = token_urlsafe(40)

    def sdk(**kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(plan_run, "OpenAI", sdk)
    with local_client(settings=PlanRunSettings(allow_network=True)) as client:
        apply(client, secret)
        response = client.post(
            "/api/plans",
            json={
                **request_body(upload(client), generate=True),
                "allow_network": True,
            },
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "planning_failed"
        assert secret not in response.text + caplog.text


@pytest.mark.parametrize("header", ["host", "origin"])
def test_duplicate_authority_headers_are_rejected(header):
    with local_client() as client:
        headers = list(HEADERS.items())
        if header == "host":
            headers.append(("Host", "127.0.0.1:8010"))
        headers.append((header, URL if header == "origin" else "127.0.0.1:8010"))
        response = client.put("/api/provider-settings", headers=headers, json={"model": MODEL})
        assert response.status_code == 403


def test_sdk_authentication_failure_does_not_log_or_return_response_credentials(
    monkeypatch, caplog
):
    secret = token_urlsafe(40)
    caplog.set_level(logging.DEBUG)

    def respond(request):
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": secret,
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
        )

    def sdk(**kwargs):
        return OpenAI(
            **kwargs, http_client=cast(Any, httpx.Client(transport=httpx.MockTransport(respond)))
        )

    monkeypatch.setattr(plan_run, "OpenAI", sdk)
    with local_client(settings=PlanRunSettings(allow_network=True)) as client:
        apply(client, secret)
        response = client.post(
            "/api/plans",
            json={**request_body(upload(client), generate=True), "allow_network": True},
        )
        assert response.status_code == 502
        assert secret not in response.text + caplog.text


def test_unexpected_configuration_errors_do_not_escape_to_server_tracebacks(monkeypatch, caplog):
    from ai_data_enrichment.provider_settings import ProviderConfiguration

    secret = token_urlsafe(40)

    def failure(self, body):
        raise RuntimeError(secret)

    monkeypatch.setattr(ProviderConfiguration, "apply", failure)
    # Default TestClient raises unhandled server errors, just as Uvicorn would log them.
    with local_client() as client:
        response = apply(client, secret)
        assert response.status_code == 500
        assert response.headers["cache-control"] == "no-store"
        assert secret not in response.text + caplog.text
        assert all(record.exc_info is None for record in caplog.records)
