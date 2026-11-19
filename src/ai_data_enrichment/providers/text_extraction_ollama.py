"""A local model as the reader of a text: the extraction request sent to Ollama.

Used by the mail probe (``scripts/experiment_mail_extraction.py``) only. It is not a
binding, no capability or setting refers to it, and nothing in ``src/`` imports it.

``OllamaClient`` satisfies the handler's ``ExtractionClient`` protocol: the handler calls
``client.responses.create(**request)`` with the request it builds for the OpenAI API, and
this module sends the same instructions, text and JSON schema to ``POST /api/chat`` and
hands back an object of the shape ``parse_reply`` reads. Every check and every mark is
therefore computed by the existing reader; nothing here judges a value.

Failures use the reader's own outcome codes, through the handler:

- a status 401 or 403 is ``SOURCE_ACCESS_DENIED``, any other 4xx (a model that is not
  pulled is a 404) is ``REQUEST_REFUSED``, a 5xx is ``PROVIDER_ERROR``;
- no connection, a timeout, a body that is not JSON, and a prompt that did not fit the
  context are ``PROVIDER_ERROR``; the fixed message names the class of the error
  (``OllamaConnectionError``, ``OllamaTimeout``, ``OllamaBadResponse``,
  ``PromptDidNotFit``) and nothing else;
- a reply cut by the length limit (``done_reason`` ``length``) is ``RESPONSE_INCOMPLETE``;
- a reply that is not the JSON asked for is ``INVALID_RESPONSE``, from ``parse_reply``.

Nothing here prints or keeps a text, a reply or an error body. Only the standard library
sends the requests; the connection ignores proxy settings and follows no redirect, because
the text of every mail goes to this address.
"""

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from ai_data_enrichment.providers.text_extraction import PROMPT_VERSION

__all__ = [
    "DEFAULT_LOAD_TIMEOUT_SECONDS",
    "DEFAULT_NUM_CTX",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_URL",
    "ModelInfo",
    "OllamaBadResponse",
    "OllamaClient",
    "OllamaConnectionError",
    "OllamaHttpError",
    "OllamaResponse",
    "OllamaTimeout",
    "OllamaUnavailable",
    "PromptDidNotFit",
    "chat_body",
    "check_base_url",
    "configuration_id",
    "is_loopback_url",
    "prompt_did_not_fit",
]

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_NUM_CTX = 8192
DEFAULT_TIMEOUT_SECONDS = 120.0
# Loading a model that is not in memory yet can take minutes (the weights are read from disk).
DEFAULT_LOAD_TIMEOUT_SECONDS = 600.0
PREFLIGHT_TIMEOUT_SECONDS = 10.0
TEMPERATURE = 0.0
# A reply is a small JSON object; this only stops a runaway body from filling the memory.
MAX_BODY_BYTES = 8 * 1024 * 1024


def configuration_id(model: str) -> str:
    """What a result depends on besides the text: the prompt wording and the model."""
    return f"ollama-text-extraction-p{PROMPT_VERSION}-{model}"


# --- Errors --------------------------------------------------------------------------


class OllamaUnavailable(Exception):
    """A request before the run (version, model list) did not get a usable answer."""


class OllamaConnectionError(Exception):
    """No connection could be made or kept."""


class OllamaTimeout(Exception):
    """The server did not answer in time."""


class OllamaBadResponse(Exception):
    """The HTTP body is not a JSON object."""


class OllamaHttpError(Exception):
    """An HTTP status of 400 or more. The body is never read, so it is never shown."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class PromptDidNotFit(Exception):
    """The prompt (with the reply) reached the context window: Ollama may have cut it."""


# --- The address ---------------------------------------------------------------------


def check_base_url(url: str, *, allow_remote: bool = False) -> str:
    """The address without a trailing slash; ValueError unless it is a loopback address.

    Only ``http`` and ``https`` with a host and no user name or password. A host that is
    not ``localhost`` or a loopback IP address is refused unless ``allow_remote``, because
    the text of every mail is sent there.
    """
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host = parts.hostname
        _ = parts.port  # raises ValueError for a bad port
    except ValueError as exc:
        raise ValueError("The Ollama address is not a valid URL.") from exc
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError("The Ollama address must be an http or https URL with a host.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("The Ollama address must not hold a user name or password.")
    if parts.query or parts.fragment or parts.path.strip("/"):
        raise ValueError("The Ollama address is the server only, without a path.")
    if not allow_remote and not _is_loopback(host):
        raise ValueError(
            "The Ollama address is not a loopback address; the text of every mail would leave "
            "this machine. Use --ollama-allow-remote only if that is intended."
        )
    return f"{parts.scheme}://{parts.netloc}"


def is_loopback_url(url: str) -> bool:
    """Whether the host of ``url`` is this machine (``localhost`` or a loopback address)."""
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    return bool(host) and _is_loopback(host)


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# --- The request ---------------------------------------------------------------------


def chat_body(
    request: dict[str, Any],
    *,
    num_ctx: int,
    temperature: float = TEMPERATURE,
    think: bool | None = False,
) -> dict[str, Any]:
    """The body of ``POST /api/chat`` for the handler's request (the OpenAI shape).

    ``instructions`` becomes the system message, with the JSON schema written out after it
    (the structured outputs page advises passing the schema in the prompt too); ``input`` is
    the user message; the schema is also ``format``, which constrains the reply. ``think`` is
    off; with ``None`` the field is left out and the model decides (for a server that refuses
    the field for a model without thinking). ``num_predict`` is the request's output limit,
    at most half the context.
    """
    schema = request["text"]["format"]["schema"]
    system = (
        f"{request['instructions']}\n\n"
        "Reply with one JSON object that matches this JSON schema:\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )
    body: dict[str, Any] = {
        "model": request["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": request["input"]},
        ],
        "stream": False,
        "format": schema,
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": min(int(request["max_output_tokens"]), num_ctx // 2),
        },
    }
    if think is not None:
        body["think"] = think
    return body


def _count(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def prompt_did_not_fit(payload: dict[str, Any], num_ctx: int) -> bool:
    """Whether the prompt and the reply together used the whole context window.

    The documentation does not say what the server does with a prompt that is too long, so
    this is a heuristic, not a documented signal: a prompt that is cut, or a window that is
    shifted while the reply is written, shows as a token count at the window's size. The
    prompt tokens are ``prompt_eval_count`` plus ``prompt_eval_cached_count`` (the page does
    not say whether the first includes the second; adding both can only err towards
    reporting), and the reply's ``eval_count`` is added.
    """
    used = (
        _count(payload, "prompt_eval_count")
        + _count(payload, "prompt_eval_cached_count")
        + _count(payload, "eval_count")
    )
    return used >= num_ctx


# --- The answer ----------------------------------------------------------------------


@dataclass(frozen=True)
class _Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class OllamaResponse:
    """The parts of a Responses API object that the handler and ``parse_reply`` read."""

    status: str
    output_text: str
    usage: _Usage
    output: tuple[()] = ()


class _Opener(Protocol):
    def open(
        self, fullurl: urllib.request.Request, data: Any = None, timeout: Any = None
    ) -> Any: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def default_opener() -> _Opener:
    """No proxy from the environment and no redirect: the text goes to this address only."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


@dataclass(frozen=True)
class ModelInfo:
    name: str
    digest: str
    size: int


class _Responses:
    def __init__(self, client: "OllamaClient") -> None:
        self._client = client

    def create(self, **kwargs: Any) -> OllamaResponse:
        return self._client.chat(kwargs)


class OllamaClient:
    """``client.responses.create(**request)`` answered by a local Ollama server."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        num_ctx: int = DEFAULT_NUM_CTX,
        temperature: float = TEMPERATURE,
        think: bool | None = False,
        allow_remote: bool = False,
        opener: _Opener | None = None,
    ) -> None:
        self.url = check_base_url(url, allow_remote=allow_remote)
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.think = think
        self._opener = opener or default_opener()
        self.responses = _Responses(self)
        # Numbers about the prompts that did not fit; set by every ``chat``.
        self.last_prompt_did_not_fit = False
        self.prompts_did_not_fit = 0
        # Whether the last ``chat`` ended in a timeout or a lost connection (the probe asks
        # such a mail once more); reset by every ``chat``.
        self.last_transport_failure = False

    # -- the transport ----------------------------------------------------------------

    def _send(
        self, method: str, path: str, body: dict[str, Any] | None, timeout: float
    ) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.url + path,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method=method,
        )
        try:
            with self._opener.open(request, timeout=timeout) as reply:
                raw = reply.read(MAX_BODY_BYTES + 1)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise OllamaHttpError(exc.code) from None
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            if isinstance(reason, TimeoutError):
                raise OllamaTimeout("The server did not answer in time.") from None
            raise OllamaConnectionError("No connection to the server.") from None
        if len(raw) > MAX_BODY_BYTES:
            raise OllamaBadResponse("The answer is too large.")
        try:
            payload = json.loads(raw)
        except (ValueError, RecursionError):
            raise OllamaBadResponse("The answer is not JSON.") from None
        if not isinstance(payload, dict):
            raise OllamaBadResponse("The answer is not a JSON object.")
        return payload

    # -- before the run ---------------------------------------------------------------

    def version(self) -> str:
        """``GET /api/version``; ``OllamaUnavailable`` when it gives no version."""
        try:
            version = self._send("GET", "/api/version", None, PREFLIGHT_TIMEOUT_SECONDS)["version"]
        except (OllamaConnectionError, OllamaTimeout, OllamaBadResponse, OllamaHttpError, KeyError):
            raise OllamaUnavailable("No version from the server.") from None
        if not isinstance(version, str):
            raise OllamaUnavailable("No version from the server.")
        return version

    def find_model(self, name: str) -> ModelInfo | None:
        """The entry of ``GET /api/tags`` for ``name`` (``:latest`` when no tag is given)."""
        try:
            models = self._send("GET", "/api/tags", None, PREFLIGHT_TIMEOUT_SECONDS)["models"]
        except (OllamaConnectionError, OllamaTimeout, OllamaBadResponse, OllamaHttpError, KeyError):
            raise OllamaUnavailable("No model list from the server.") from None
        if not isinstance(models, list):
            raise OllamaUnavailable("No model list from the server.")
        wanted = name if ":" in name else f"{name}:latest"
        for item in models:
            if not isinstance(item, dict):
                continue
            listed = item.get("name") or item.get("model")
            if listed in (name, wanted):
                digest, size = item.get("digest"), item.get("size")
                return ModelInfo(
                    name=str(listed),
                    digest=digest if isinstance(digest, str) else "",
                    size=size if isinstance(size, int) and not isinstance(size, bool) else 0,
                )
        return None

    def load_model(self, name: str, timeout: float) -> None:
        """Load ``name`` into memory with a request that asks for no answer.

        The FAQ (https://docs.ollama.com/faq.md, "How can I preload a model into Ollama to
        get faster response times?") says a model is preloaded "by sending the Ollama server
        an empty request": ``POST /api/chat`` with the model name and no ``messages``. The
        same ``num_ctx`` as the later requests is sent, so that the model is loaded the way it
        will be used (assumption: the server may reload a model whose window differs; the
        documentation read does not say). ``keep_alive`` is left out: the server's default
        (five minutes after the last request) covers a run, which asks one mail after another.
        ``OllamaUnavailable`` when the server answers with an error or not in time.
        """
        try:
            self._send(
                "POST",
                "/api/chat",
                {"model": name, "stream": False, "options": {"num_ctx": self.num_ctx}},
                timeout,
            )
        except (OllamaConnectionError, OllamaTimeout, OllamaBadResponse, OllamaHttpError):
            raise OllamaUnavailable("The model was not loaded.") from None

    # -- the call ---------------------------------------------------------------------

    def chat(self, request: dict[str, Any]) -> OllamaResponse:
        """One non-streaming ``POST /api/chat`` for the handler's request."""
        self.last_prompt_did_not_fit = False
        self.last_transport_failure = False
        timeout = request.get("timeout")
        try:
            payload = self._send(
                "POST",
                "/api/chat",
                chat_body(
                    request, num_ctx=self.num_ctx, temperature=self.temperature, think=self.think
                ),
                float(timeout) if isinstance(timeout, int | float) else DEFAULT_TIMEOUT_SECONDS,
            )
        except (OllamaTimeout, OllamaConnectionError):
            self.last_transport_failure = True
            raise
        if prompt_did_not_fit(payload, self.num_ctx):
            self.last_prompt_did_not_fit = True
            self.prompts_did_not_fit += 1
            raise PromptDidNotFit("The prompt reached the context window.")
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        # ``message.thinking`` is never read: only the answer is, and the reader checks it.
        cut = payload.get("done") is not True or payload.get("done_reason") == "length"
        return OllamaResponse(
            status="incomplete" if cut else "completed",
            output_text=content if isinstance(content, str) else "",
            usage=_Usage(_count(payload, "prompt_eval_count"), _count(payload, "eval_count")),
        )
