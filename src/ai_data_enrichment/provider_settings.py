"""Per-process OpenAI configuration; never serialize credentials into workflow state."""

import asyncio
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ai_data_enrichment.workspace_errors import WorkspaceError

# Deliberately bounded to models supported by the existing Responses JSON adapter.
if TYPE_CHECKING:
    from ai_data_enrichment.plan_run import PlanRunSettings

# gpt-4o-mini was removed after repeated live runs on 2026-11-11: it produced 1 valid
# Mountains plan in 20 (false "unsupported" on the first attempt) against 10 of 10 for
# gpt-4.1-mini. See docs/26-live-planning-reliability.md before extending this list.
SUPPORTED_MODELS = ("gpt-4.1-mini", "gpt-4.1")
DEFAULT_MODEL = "gpt-4.1-mini"
LOCAL_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8010",
    "http://localhost:8010",
)


class ConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    model: str | None = Field(default=None, max_length=64)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=512)


@dataclass(repr=False)
class ProviderConfiguration:
    model: str | None
    _key: SecretStr | None = field(repr=False)

    @classmethod
    def from_environment(cls, model: str | None) -> "ProviderConfiguration":
        key = os.getenv("OPENAI_API_KEY")
        # No configured model selects the measured default. An explicitly configured
        # but unsupported model stays unset, so the mismatch is reported, not hidden.
        selected = DEFAULT_MODEL if model is None else model if model in SUPPORTED_MODELS else None
        return cls(selected, SecretStr(key) if key else None)

    @property
    def key_present(self) -> bool:
        return self._key is not None

    def api_key(self) -> str | None:
        return self._key.get_secret_value() if self._key else None

    def apply(self, body: ConfigurationRequest) -> None:
        if body.model is not None and body.model not in SUPPORTED_MODELS:
            raise WorkspaceError("unsupported_model", "Choose a supported OpenAI planning model.")
        if body.api_key is not None:
            key = body.api_key.get_secret_value()
            if not key.isascii() or any(c.isspace() or ord(c) < 33 for c in key):
                raise WorkspaceError(
                    "invalid_api_key", "Enter a non-empty API key without whitespace."
                )
        self.model = body.model
        # Omitted/null key means keep; explicit remove is a separate action.
        if body.api_key is not None:
            self._key = body.api_key

    def remove_key(self) -> None:
        self._key = None

    def readiness(self, *, network: bool, offline: bool, configurable: bool) -> dict:
        return {
            "provider": "openai",
            "supported_models": [{"id": m, "label": m} for m in SUPPORTED_MODELS],
            "model": self.model,
            "key_present": self.key_present,
            "server_network_allowed": network,
            "live_ready": network and self.model is not None and self.key_present,
            "planning_mode": "offline_injected" if offline else "live",
            "configuration_enabled": configurable,
            "storage": "process_memory",
        }


def _loopback(host: str | None) -> bool:
    # Literal addresses only: never resolve an attacker-controlled DNS name.
    return host in {"127.0.0.1", "::1", "localhost"}


def require_local_configuration(request: Request, *, enabled: bool) -> None:
    """Same-origin browser handoff, including Vite's unchanged Host/Origin proxy pair.

    The supported launcher binds loopback and disables forwarded-header trust.
    Generic ASGI deployments default to configuration disabled, even on localhost.
    """
    host = request.headers.get("host", "")
    origin = request.headers.get("origin", "")
    server = request.scope.get("server")
    if (
        not enabled
        or not request.client
        or not _loopback(request.client.host)
        or not server
        or not _loopback(server[0])
        or len(request.headers.getlist("host")) != 1
        or len(request.headers.getlist("origin")) != 1
        or origin not in LOCAL_ORIGINS
        or urlsplit(origin).netloc != host
        or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"
        or any(
            h.lower() == "forwarded" or h.lower().startswith("x-forwarded-")
            for h in request.headers
        )
        or request.headers.get("x-enrichment-config") != "1"
    ):
        raise WorkspaceError(
            "configuration_forbidden",
            "Browser configuration requires the trusted local launcher.",
            403,
        )


def configuration_router(
    config: ProviderConfiguration,
    lock: asyncio.Lock,
    *,
    settings: "PlanRunSettings",
    offline: bool,
    enabled: bool,
) -> APIRouter:
    router = APIRouter(prefix="/api/provider-settings")

    def status():
        return config.readiness(
            network=settings.allow_network, offline=offline, configurable=enabled
        )

    @router.get("")
    async def readiness():
        return status()

    # Read/validate the body only after the access check; never include raw input in errors.
    @router.put("")
    async def apply(request: Request):
        require_local_configuration(request, enabled=enabled)
        if request.headers.get("content-type") != "application/json":
            raise WorkspaceError("invalid_request", "Use application/json.", 415)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 2048:
                raise WorkspaceError(
                    "request_too_large", "Configuration exceeds its size limit.", 413
                )
        try:
            body = ConfigurationRequest.model_validate_json(raw)
        except ValueError:
            raise WorkspaceError("invalid_request", "Check the configuration fields.") from None
        async with lock:
            config.apply(body)
            return status()

    @router.delete("/key")
    async def remove(request: Request):
        require_local_configuration(request, enabled=enabled)
        async with lock:
            config.remove_key()
            return status()

    return router
