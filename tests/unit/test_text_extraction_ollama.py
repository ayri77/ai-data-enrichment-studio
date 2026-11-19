"""The Ollama client for the mail probe, against a fake HTTP opener: no server, no network."""

import ast
import io
import json
import urllib.error
import urllib.request
from http.client import HTTPMessage
from pathlib import Path
from typing import Any

import pytest
from tests.support.ollama import FakeOpener, chat_payload, http_error
from tests.support.text_extraction import TEXT, good_answer, mixed_task

from ai_data_enrichment.capabilities import bindings
from ai_data_enrichment.providers import text_extraction_ollama as ollama
from ai_data_enrichment.providers.text_extraction import (
    INSTRUCTION,
    ExtractionUsage,
    answer_schema,
    user_message,
)
from ai_data_enrichment.providers.text_extraction_handler import TextExtractionHandler
from ai_data_enrichment.providers.text_extraction_ollama import OllamaClient
from ai_data_enrichment.providers.text_extraction_reply import ExtractionResult

SRC = Path(__file__).parents[2] / "src" / "ai_data_enrichment"
SECRET_BODY = "secret body text from the server"


def read(
    *items: Any, num_ctx: int = 8192, text: str = TEXT
) -> tuple[ExtractionResult, FakeOpener, OllamaClient]:
    opener = FakeOpener(*items)
    client = OllamaClient(num_ctx=num_ctx, opener=opener)
    handler = TextExtractionHandler(
        client, model="m", reasoning_effort=None, usage=ExtractionUsage(), timeout_seconds=7
    )
    return handler.extract(mixed_task(), text), opener, client


# --- a good reply: every check and mark comes from the existing reader --------------------


def test_a_good_reply_for_every_kind_of_column() -> None:
    result, _, _ = read(chat_payload(good_answer(), prompt=812, evals=64))
    assert result.outcome == "FOUND" and not result.dropped
    by_column = {v.column: (v.value, v.mark) for v in result.kept}
    assert by_column == {
        "Service": ("Cloudbox Plus", "literal"),
        "Amount": (9.99, "literal"),
        "Paid on": ("2026-11-03", "literal"),
        "Auto renews": ("Yes", "derived"),
        "Event": ("payment", "derived"),
    }
    assert (result.input_tokens, result.output_tokens) == (812, 64)


def test_the_request_is_the_extraction_request_in_the_chat_shape() -> None:
    _, opener, _ = read(chat_payload(good_answer()))
    (call,) = opener.calls
    task = mixed_task()
    assert (call.method, call.url) == ("POST", "http://127.0.0.1:11434/api/chat")
    assert call.timeout == 7
    body = call.body
    assert body is not None
    assert set(body) == {"model", "messages", "stream", "think", "format", "options"}
    assert body["model"] == "m" and body["stream"] is False and body["think"] is False
    assert body["format"] == answer_schema(task)
    assert body["options"] == {"temperature": 0.0, "num_ctx": 8192, "num_predict": 4048}
    system, user = body["messages"]
    assert system["role"] == "system" and system["content"].startswith(INSTRUCTION)
    assert (
        json.dumps(answer_schema(task), ensure_ascii=False, separators=(",", ":"))
        in (system["content"])
    )
    assert user == {"role": "user", "content": user_message(task, TEXT)}
    assert "authorization" not in call.headers


def test_the_output_limit_never_exceeds_half_of_the_context() -> None:
    _, opener, _ = read(chat_payload(good_answer()), num_ctx=4096)
    body = opener.calls[0].body
    assert body is not None
    assert body["options"] == {"temperature": 0.0, "num_ctx": 4096, "num_predict": 2048}


def test_not_applicable() -> None:
    answer = {"applies": False, "values": good_answer()["values"]}
    result, _, _ = read(chat_payload(answer))
    assert result.outcome == "NOT_APPLICABLE" and not result.kept


def test_a_quote_that_is_not_in_the_text_is_dropped_by_the_existing_reader() -> None:
    answer = good_answer()
    answer["values"]["value_1"] = {"value": "Cloudbox Plus", "quote": "Cloudbox Plus Family"}
    result, _, _ = read(chat_payload(answer))
    assert result.outcome == "FOUND"
    assert [(d.column, d.reason) for d in result.dropped] == [("Service", "quote_not_in_text")]
    assert "Service" not in {v.column for v in result.kept}


@pytest.mark.parametrize("content", ["not json at all", "", '{"applies": true}', "[1]"])
def test_a_reply_that_is_not_the_json_asked_for(content: str) -> None:
    result, _, _ = read(chat_payload(content))
    assert result.outcome == "INVALID_RESPONSE"


def test_thinking_text_is_never_read() -> None:
    thinking = "THINKING-MARKER: the amount is probably 12.00"
    result, _, _ = read(chat_payload(good_answer(), thinking=thinking))
    assert result.outcome == "FOUND" and thinking not in repr(result)
    # Thinking without an answer is no answer.
    empty, _, _ = read(chat_payload("", thinking=json.dumps(good_answer())))
    assert empty.outcome == "INVALID_RESPONSE"


# --- failures map to the reader's codes ---------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [(404, "REQUEST_REFUSED"), (400, "REQUEST_REFUSED"), (401, "SOURCE_ACCESS_DENIED"),
     (429, "PROVIDER_ERROR"), (500, "PROVIDER_ERROR"), (502, "PROVIDER_ERROR")],
)  # fmt: skip
def test_http_errors_and_a_missing_model(status: int, code: str) -> None:
    result, _, _ = read(http_error(status))
    assert result.outcome == code
    assert SECRET_BODY not in repr(result)
    if status == 404:
        assert "404" in result.message


def test_connection_refused() -> None:
    refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    result, _, _ = read(refused)
    assert result.outcome == "PROVIDER_ERROR" and "OllamaConnectionError" in result.message
    assert "111" not in result.message


def test_a_timeout() -> None:
    for error in (TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))):
        result, _, _ = read(error)
        assert result.outcome == "PROVIDER_ERROR" and "OllamaTimeout" in result.message


@pytest.mark.parametrize("body", [b"<html>proxy page</html>", b"[1, 2]"])
def test_an_http_body_that_is_not_a_json_object(body: bytes) -> None:
    result, _, _ = read(body)
    assert result.outcome == "PROVIDER_ERROR" and "OllamaBadResponse" in result.message
    assert "proxy" not in repr(result)


def test_a_reply_cut_by_the_length_limit() -> None:
    result, _, _ = read(
        chat_payload('{"applies": tr', prompt=900, evals=4048, done_reason="length")
    )
    assert result.outcome == "RESPONSE_INCOMPLETE"
    assert (result.input_tokens, result.output_tokens) == (900, 4048)
    unfinished = {**chat_payload(good_answer()), "done": False}
    assert read(unfinished)[0].outcome == "RESPONSE_INCOMPLETE"


# --- a prompt that did not fit the context ------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "evals", "cached", "fits"),
    [(1000, 100, None, True), (8000, 100, None, True), (8100, 92, None, False),
     (8192, 10, None, False), (4096, 90, 4100, False), (4096, 90, 100, True)],
)  # fmt: skip
def test_prompt_did_not_fit(prompt: int, evals: int, cached: int | None, fits: bool) -> None:
    payload = chat_payload(good_answer(), prompt=prompt, evals=evals, cached=cached)
    assert ollama.prompt_did_not_fit(payload, 8192) is (not fits)
    result, _, client = read(payload)
    if fits:
        assert result.outcome == "FOUND" and client.prompts_did_not_fit == 0
    else:
        # Not answered from half a text, and not mistaken for any other failure.
        assert result.outcome == "PROVIDER_ERROR" and "PromptDidNotFit" in result.message
        assert not result.kept and client.prompts_did_not_fit == 1
        assert client.last_prompt_did_not_fit


def test_the_flag_follows_the_last_call() -> None:
    opener = FakeOpener(chat_payload(good_answer(), prompt=8192), chat_payload(good_answer()))
    client = OllamaClient(opener=opener)
    handler = TextExtractionHandler(
        client, model="m", reasoning_effort=None, usage=ExtractionUsage()
    )
    assert handler.extract(mixed_task(), TEXT).outcome == "PROVIDER_ERROR"
    assert client.last_prompt_did_not_fit
    assert handler.extract(mixed_task(), TEXT).outcome == "FOUND"
    assert not client.last_prompt_did_not_fit and client.prompts_did_not_fit == 1


# --- the address --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:11434", "http://localhost:11434/", "http://[::1]:11434",
     "http://127.0.0.2:8080", "https://127.0.0.1"],
)  # fmt: skip
def test_loopback_addresses_are_accepted(url: str) -> None:
    assert ollama.check_base_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    ["http://example.com:11434", "http://192.168.1.20:11434", "http://10.0.0.5", "http://[::2]",
     "http://localhost.example.com", "http://0.0.0.0:11434"],
)  # fmt: skip
def test_a_remote_host_is_refused_before_anything_is_sent(url: str) -> None:
    opener = FakeOpener(chat_payload(good_answer()))
    with pytest.raises(ValueError, match="loopback"):
        OllamaClient(url, opener=opener)
    assert opener.calls == []
    assert OllamaClient(url, allow_remote=True, opener=opener).url == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    ["ftp://127.0.0.1", "127.0.0.1:11434", "http://user:pw@127.0.0.1", "http://127.0.0.1/api",
     "http://127.0.0.1:99999", "http://", ""],
)  # fmt: skip
def test_other_addresses_are_refused_even_when_remote_is_allowed(url: str) -> None:
    with pytest.raises(ValueError):
        ollama.check_base_url(url, allow_remote=True)


def test_the_real_opener_ignores_proxies_and_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("http_proxy", "http://proxy.example.com:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.com:3128")

    def has_proxy(opener: urllib.request.OpenerDirector) -> bool:
        return any(isinstance(h, urllib.request.ProxyHandler) for h in vars(opener)["handlers"])

    # The ordinary opener would use the proxy of the environment; this one does not.
    assert has_proxy(urllib.request.build_opener())
    opener = ollama.default_opener()
    assert isinstance(opener, urllib.request.OpenerDirector)
    handlers = vars(opener)["handlers"]
    assert not has_proxy(opener)
    redirect = next(h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
    request = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=b"{}")
    moved = redirect.redirect_request(
        request, io.BytesIO(), 307, "x", HTTPMessage(), "http://example.com/"
    )
    assert moved is None


# --- before a run -------------------------------------------------------------------------

TAGS = {
    "models": [
        {"name": "alpha:latest", "model": "alpha:latest", "digest": "sha256:abc", "size": 123},
        {"name": "beta:7b", "model": "beta:7b", "digest": "sha256:def", "size": 456},
    ]
}


def test_version_and_model_list() -> None:
    opener = FakeOpener({"version": "0.12.6"}, TAGS, TAGS, TAGS)
    client = OllamaClient(opener=opener)
    assert client.version() == "0.12.6"
    found = client.find_model("alpha")
    assert found == ollama.ModelInfo("alpha:latest", "sha256:abc", 123)
    assert client.find_model("beta:7b") == ollama.ModelInfo("beta:7b", "sha256:def", 456)
    assert client.find_model("beta") is None
    assert [(c.method, c.url) for c in opener.calls] == [
        ("GET", "http://127.0.0.1:11434/api/version"),
        ("GET", "http://127.0.0.1:11434/api/tags"),
        ("GET", "http://127.0.0.1:11434/api/tags"),
        ("GET", "http://127.0.0.1:11434/api/tags"),
    ]
    assert all(c.body is None for c in opener.calls)


@pytest.mark.parametrize(
    "item",
    [urllib.error.URLError(ConnectionRefusedError()), http_error(500), b"nope", {"other": 1},
     {"version": 12}, {"models": "x"}],
)  # fmt: skip
def test_a_server_that_does_not_answer_is_unavailable(item: Any) -> None:
    client = OllamaClient(opener=FakeOpener(item, item))
    with pytest.raises(ollama.OllamaUnavailable):
        client.version()
    with pytest.raises(ollama.OllamaUnavailable):
        client.find_model("alpha")


def test_configuration_id() -> None:
    assert ollama.configuration_id("alpha") == "ollama-text-extraction-p1-alpha"


# --- outside the application --------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{alias.name}" for alias in node.names}
    return names


def test_no_application_code_imports_the_client() -> None:
    importers = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path.name != "text_extraction_ollama.py"
        and any("text_extraction_ollama" in name for name in _imports(path))
    ]
    assert importers == []
    live = _imports(SRC / "orchestration" / "live_executor.py")
    assert not any("ollama" in name for name in live)


def test_it_is_not_a_binding_and_adds_no_setting_or_route() -> None:
    every = (
        *bindings.BINDINGS,
        *bindings.FALLBACK_BINDINGS,
        *bindings.EXTRACTION_BINDINGS,
    )
    assert not any("ollama" in repr(binding).lower() for binding in every)
    assert "ollama" not in Path(bindings.__file__).read_text(encoding="utf-8").lower()
    # No setting, route or mention anywhere else in the application's code.
    mentions = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path.name != "text_extraction_ollama.py"
        and "ollama" in path.read_text(encoding="utf-8").lower()
    ]
    assert mentions == []


def test_it_uses_the_standard_library_for_http() -> None:
    names = _imports(SRC / "providers" / "text_extraction_ollama.py")
    roots = {name.split(".")[0] for name in names}
    assert roots <= {"ipaddress", "json", "urllib", "dataclasses", "typing", "ai_data_enrichment"}
