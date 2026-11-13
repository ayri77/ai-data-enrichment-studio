# Provider / Model settings (#113)

The existing Studio dialog now configures OpenAI planning end to end. The backend
holds the model and API key in process memory. There is no secret file, browser
persistence, task preset, key-return API, OS keychain, or credential export.
Only OpenAI is supported. The bounded model picker currently allows
the models of the catalog (`planning/model_catalog.py`): `gpt-6-luna` (the default when
`OPENAI_MODEL` is unset), `gpt-4.1-mini` and `gpt-4.1`, using the existing
[Responses JSON adapter](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
Configuration readiness does not verify billing, credentials, model access, or
connectivity: those are checked by the actual planning request.

## Start the local Studio

From the repository root, with Python 3.12+, uv, and Node installed:

```powershell
uv sync --locked --group dev
uv run --locked python -m ai_data_enrichment.local_server --allow-network
```

In a second terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. No `OPENAI_MODEL` or `OPENAI_API_KEY` environment
setup is needed. `--allow-network` opens the server gate; omitting it keeps live
requests disabled (unless `ENRICHMENT_ALLOW_NETWORK=1` was already set). Browser
settings cannot change that gate. External-request consent in Dataset remains
separate, unchecked by default. Saving settings never calls OpenAI, creates a
plan, approves anything, or starts execution.

The original `uvicorn ai_data_enrichment.api:app --host 127.0.0.1 --port 8010`
entry point still works with environment startup defaults, but browser credential
configuration is **disabled** there. Use the local launcher for the dialog.

## Trust boundary

This is a local single-user feature, not hosted multi-user authentication. The
supported launcher binds **127.0.0.1:8010** explicitly, runs one process, and
turns off Uvicorn's forwarded-header trust. Keep Vite bound to loopback as in
`npm run dev`; do not expose either server through LAN, tunnels, remote proxies,
or shared hosting. Local HTTP transports a key only across the local loopback
connection; there is no HTTP key-storage service. Trusted local applications and
browser extensions can still inspect local process/browser memory. Memory is
released normally, not cryptographically zeroized.

Mutation requires local mode, a loopback peer and server address, exactly one
Host and Origin, an exact approved Origin with the same Host, a custom
`X-Enrichment-Config: 1` header, and no cross-site fetch metadata or forwarded
headers. The fixed approved origins are `http://127.0.0.1:5173`,
`http://localhost:5173`, `http://127.0.0.1:8010`, and `http://localhost:8010`.
The supported Vite proxy preserves Host and Origin (`changeOrigin` is false);
the browser test exercises that actual proxy path. Arbitrary loopback ports,
null/absent origins, DNS-rebinding hosts, LAN peers and forwarded requests fail
closed. There is no CORS middleware or wildcard allowlist. Non-browser local
clients would need to supply the same headers; this is not a defense against
other software already trusted to run as the local user.

Do not enable request-body/header capture, HTTP debug proxies, session recording,
telemetry, or browser password saving for credentials. The input is masked,
uncontrolled (outside React/workflow state), and cleared immediately after
handoff, on settings-tab unmount, and on dialog close. The server never returns
an existing key or a key fragment. Configuration requests and readiness responses
use no-store; keys are never put in URLs. Application errors expose stable codes,
and unexpected errors log only their exception type, without exception text or
traceback that could contain credentials.

Environment `OPENAI_MODEL` (supported identifiers only) and `OPENAI_API_KEY` are
optional **startup snapshots**. To avoid entering the key after every restart, keep both
in the ignored `.env` and start with `uv run --locked --env-file .env python -m
ai_data_enrichment.local_server --allow-network`. An `OPENAI_MODEL` outside the
supported list is not applied: the model stays unset, Build Plan stays disabled
and the Dataset screen says that a supported model must be chosen. Apply replaces runtime values; an empty input
keeps the current key. Remove clears the runtime key without falling back to the
environment. Restart restores environment defaults if they still exist; otherwise
model/key are absent. Closing/reloading the browser does not clear backend memory.
Changing configuration preserves the active dataset, existing plan fingerprints,
approvals and jobs; every new generated plan still requires explicit approval.
The key is passed only to the planning SDK, never to execution workers or reports.
The planning SDK uses the fixed OpenAI HTTPS endpoint, not an environment-selected
base URL.

## API contract

| Method / route | Behavior |
| --- | --- |
| `GET /api/provider-settings` | Bounded non-secret readiness, no external request |
| `PUT /api/provider-settings` | Trusted local JSON `{ "model": "gpt-4.1-mini", "api_key": "<new-key>" }`; omit/null key to retain, null model to deselect |
| `DELETE /api/provider-settings/key` | Trusted local removal, no environment fallback |

PUT requires `Content-Type: application/json`; mutation headers include matching
Origin/Host and `X-Enrichment-Config: 1`. JSON is limited to 2 KiB, keys to 512
ASCII characters without whitespace, and model IDs to the fixed supported list.
Invalid requests never echo input. Mutation returns the same readiness shape:

```json
{
  "provider": "openai",
  "supported_models": [{"id": "gpt-4.1-mini", "label": "gpt-4.1-mini"}],
  "model": "gpt-4.1-mini",
  "key_present": true,
  "server_network_allowed": true,
  "live_ready": true,
  "planning_mode": "live",
  "configuration_enabled": true,
  "storage": "process_memory"
}
```

`planning_mode: offline_injected` explicitly identifies injected offline model
responses; it is never reported as a real OpenAI call. `live_ready` requires all
three live gates (server network, supported model, key), irrespective of fixture
mode or per-request consent. The UI explains readiness lookup failures, disabled
server network, missing model/key and absent consent separately. Actual model
failures remain `planning_failed`; backend checks on every request are authoritative.
Submitted-plan validation continues to work with network and consent unchecked.

## Automated acceptance (no real tokens or external HTTP)

```powershell
uv run --locked pytest tests/integration/test_provider_settings.py tests/integration/test_plan_run_api.py -q
cd frontend
npm test
npm run test:e2e
```

The backend test uses the real OpenAI SDK, Responses adapter, LLMPlanner and
validator with an injected HTTP response. It then explicitly approves and runs
with offline provider responses, inspecting reports and XLSX artifacts for secret
leakage. The browser settings project starts the actual local launcher and Vite
on 8010/5173 and injects only OpenAI HTTP responses in a **test-only process**.
It applies settings through the real proxy and builds a validated draft without
auto-approval. The existing offline workflow tests also start 8011/5174.
Keep all four ports free. The settings project disables trace/video/screenshots
and does not attach request bodies or keys to artifacts.

## Optional genuine OpenAI acceptance

This is manual and opt-in; it can incur API charges and sends the instruction,
column profile and bounded sample records to OpenAI. Use only an approved sample.
Do not record the browser, copy keys into commands, capture request bodies or
include credentials in screenshots, issue comments, reports or Git.

1. Stop test servers. Start the local Studio with the commands above. Leave
   `OPENAI_MODEL` and `OPENAI_API_KEY` unset for this acceptance; do not unset them
   by printing their previous values. Use the normal local launcher, not the
   injected browser fixture module.
2. Upload `samples/capitals.xlsx`; preview Sheet1, `A1:E11`. Enter
   "Calculate distances in km between the literal city pairs; target distance."
   Choose existing output `E · distance`. Leave supplied-plan JSON empty.
3. Open **Provider / Model settings**. Select `gpt-4.1-mini`, enter your own OpenAI
   API key in the masked field, and Apply. Expect an empty key field, key-present
   status and live configuration ready. No model request has happened yet.
4. Close settings. Build Plan stays disabled until you explicitly check external
   network consent in Dataset. Check consent, then Build Plan. Expect a generated
   draft, a real DAG, and successful validation. A provider failure stays explicit;
   inspect account/model access or replace the key and retry. No offline fallback
   exists. A model may return clarification or an invalid plan; refine/retry and
   do not approve an invalid draft.
5. Confirm no execution occurred and the plan still needs approval. Optionally
   Validate again, then inspect Diagnostics for the exact fingerprint and source
   hash. Do not run providers unless separately desired; a run requires explicit
   approval and a separate Start action.
6. Return to settings: the input stays empty. Replace the key if desired; Apply
   keeps the dataset, language/theme and current plan intact. Remove the key,
   close settings and return to Dataset: generated planning is disabled with a
   missing-key explanation. Submitted-plan offline validation still works.
7. Restart the backend with no credential environment defaults. Refresh readiness:
   key and model are absent. Browser reload alone preserves backend configuration.

A successful injected test is evidence of the integration path, not proof of a
real account's credentials, model entitlement, live quality or provider uptime.
