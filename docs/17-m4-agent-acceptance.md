# M4-4 — Offline Agent Acceptance & Observability

## Purpose and boundaries

M4-4 provides a reproducible, **network-free developer demonstration** of
the bounded LangGraph orchestration layer. The sample uses a supplied plan,
a real `PlanValidator`, real `JobExecutor`, the local
`GeodesicDistanceHandler` and a deterministic in-memory geocoding fixture.

The demonstration does **not** call an LLM, modify an Excel workbook,
query live geography providers, or execute M3 human approval workflows.
In `repair` mode, `OfflineRepairer` is a controlled test stand-in for
the already-supported optional LLM plan-repair adapter. The run verifies
graph routing and revalidation, **not** LLM proposal quality or independent
factual accuracy. M3 Excel functionality and audited human review are
separately exercised by M3 demos/tests.

## Getting started

From the repository root:

```powershell
uv sync --locked --group dev
uv run python -m scripts.demo_m4_agent --mode happy
uv run python -m scripts.demo_m4_agent --mode repair
```

Both commands return a JSON report to stdout and exit with code 0 on
`completed` or 1 otherwise. Neither requires API keys or network access.

| Mode | Expected transitions | Repair attempts |
| --- | --- | --- |
| `happy` | `obtain_plan → validate → execute` | 0 |
| `repair` | `obtain_plan → validate → repair_plan → validate → execute` | 1 |

The repair run starts with an unsupported capability, records an
`UNSUPPORTED_CAPABILITY` validation diagnostic, repairs the proposed plan
and validates the replacement **before** execution. Both runs use one
Berlin/Kyiv input row and three DAG operations, yielding a Haversine
distance of approximately 1204.46 km from fixed fixture coordinates.
The value illustrates a deterministic calculation, not a verified
geographical ground-truth benchmark.

### Local observability

`graph.stream(state, stream_mode="updates")` emits node updates.
The demo turns these into `trace` entries with `node`, `status`,
`repair_attempts` and `diagnostics`. Its JSON also includes
`final_status`, `distance_km` and the existing `JobExecutionMetrics`.
This trace is local and works even when LangSmith is disabled.

## Optional LangSmith tracing

The project does not require cloud tracing. To opt in, copy
`.env.example` to a local `.env` (already ignored by Git) and set:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your-key-from-the-same-region>
LANGSMITH_PROJECT=ai-data-enrichment-studio
LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com
```

For a US-hosted LangSmith organization instead use
`LANGSMITH_ENDPOINT=https://api.smith.langchain.com`. The API key,
workspace and endpoint must refer to the same regional organization;
mixing regions can produce HTTP `403 Forbidden`.

Then run:

```powershell
uv run --env-file .env python -m scripts.demo_m4_agent --mode happy
uv run --env-file .env python -m scripts.demo_m4_agent --mode repair
```

View the project and its run traces in the matching LangSmith UI:
EU: https://eu.smith.langchain.com/ ; US: https://smith.langchain.com/ .
Tracing is enabled by environment variables; there is no custom
monitoring server or required LangSmith integration in CI. Provider and
LLM token-cost metrics are not available in this deterministic demo,
since no LLM calls are made.

**Data note:** Enabling cloud tracing may send node inputs, outputs and
other invocation metadata to LangSmith. Do not enable it for
confidential or personal datasets without an appropriate data policy
and tracing controls. Never commit API keys.

## Acceptance and non-goals

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests/integration/test_m4_demo.py -v
uv run pytest -q
```

`test_m4_demo.py` launches both modes in separate Python processes,
explicitly disables LangSmith in those subprocesses and asserts
the transition order, repair count, diagnostics, status, executor
metrics and numeric output. Existing agent integration/unit tests
exercise bounded invalid-plan routing, terminal outcomes and the
SQLite execution journal. Source workbook mutation is not part
of this demo: it contains no workbook I/O.

The controlled M4 recovery journal and its deliberate limitations are
documented in [M4-3](16-m4-controlled-recovery.md). In particular,
there is no full LangGraph checkpointer, automatic mid-DAG resume,
distributed scheduler or guarantee of exactly-once external API calls.

M4-4 adds **no** additional provider engine, monitoring infrastructure,
production-grade policy system or live evaluation benchmark.
