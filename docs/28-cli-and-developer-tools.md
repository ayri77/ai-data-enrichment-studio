# Command-line and developer tools

These sections lived in the README while milestones M2–M4 were built. They
describe the command-line interface, the benchmarks, the experiment scripts and
the developer demos. Status remarks inside them describe the time they were
written; the current state of the application is in the [README](../README.md).

Commands are run from the repository root.

## Agentic workflow developer demo (M4)

Two offline, deterministic scenarios exercise the real LangGraph validator and
JobExecutor, with fixture geocoding and local distance calculation:

```powershell
uv run python -m scripts.demo_m4_agent --mode happy
uv run python -m scripts.demo_m4_agent --mode repair
```

The second mode includes validation rejection, a bounded repair attempt and
mandatory revalidation. The JSON report includes node transitions, diagnostics,
repair count, final status and job metrics. Optional LangSmith cloud tracing
can be enabled via `.env` with `LANGSMITH_ENDPOINT` set for the correct
region (EU or US); it is not needed for tests or offline use.
See [M4-4 acceptance and tracing](17-m4-agent-acceptance.md) and
[execution recovery limits](16-m4-controlled-recovery.md). This demo
does not call an LLM or write an Excel workbook.

## In-memory execution (M2)

The project includes a typed, in-memory DAG execution engine
with explicit provider registration and dependency handling.

Implemented providers:
- Open-Meteo geocoding: city and country resolution to GeoPoint.
- Local geodesic distance: Haversine calculation in kilometers.
- Wikidata mountain resolution: name/country validation, bounded P31/P279 hierarchy checking, and P2044 elevation in meters with source Evidence.

Open-Meteo integration:
- Endpoint: https://geocoding-api.open-meteo.com/v1/search
- Country filtering: ISO 3166-1 alpha-2
- Request timeout: 10 seconds
- Maximum search results: 10
- No API key required
- Explicit error outcomes and source evidence

HTTP-based providers share a JSON transport protocol and typed transport
failures for timeout, network, HTTP status and malformed JSON. The opt-in
`RetryingJsonTransport` implements bounded transient retries and Retry-After
handling. An optional `ProviderRequestSession` now enforces per-job,
provider-specific physical HTTP attempt budgets, rate pacing and optional
uncached operation caps across different handlers. Neither is installed
automatically; the application must inject the policy-wrapped transports.
Individual providers still validate source-specific response data. An optional
registry-approved fallback coordinator requires an explicit application-owned
semantic contract, expected result type/unit and source Evidence; it is off by
default. There is no configured interchangeable fallback for current
Wikidata/Open-Meteo provider adapters.

All automated provider tests use offline fixtures or mocked HTTP
responses. Live provider checks are not required by CI.

Single-row Wikidata and Open-Meteo DAG integration tests pass offline.
Multi-row processing and cross-row reuse are implemented in `JobExecutor`
using source-independent `InputRecord` inputs and bounded batches. The job-level
cache is ephemeral and success-only. An explicitly configured local SQLite cache
adds cross-job reuse of successful results with provider-specific TTL, semantic
versions and original Evidence. Cache hits and handler-boundary invocations
have separate metrics; provider request counters distinguish actual HTTP attempts
and operations blocked by policy. Optional provider request policies expose physical HTTP
attempts, rate waits and blocked requests. Typed transient retry exhaustion
and opt-in fallback attempts/successes are separately observable (#36).

## M2 measured offline acceptance benchmark (developer tool)

The [M2 acceptance report](12-m2-acceptance.md) documents two
**1000-row, real-DAG / mocked-provider** workloads and adversarial abstention
fixtures. This is a reproducible fixture benchmark, not production external
API latency or independent factual-accuracy validation:

```bash
uv run --locked python scripts/benchmark_m2.py --rows 1000
```

The CI workflow runs this after lint, type checks and pytest. It reports
logical DAG operations, executed handlers, memo reuse, synthetic transport
attempts, per-record resolution links, Evidence counts, and measured
`JobExecutor.execute` time. It does not open network connections, read
production data, or modify workbooks. The benchmark currently measures a
*small number of repeated distinct queries*, not 1000 unique entities.

## M3 offline Excel end-to-end benchmark (developer tool)

The benchmark creates a temporary Excel workbook, executes the real
`ExcelEnrichmentRunner` and audited resolution pipeline against validated,
network-free Open-Meteo fixtures, and checks the output workbook, source
immutability, execution metrics and audit row links. This measures a
**controlled pipeline workload**, not real provider latency or independent
ground-truth correctness.

```powershell
uv run python -m scripts.benchmark_m3_excel --scenario benchmarks/scenarios/capitals_repeated.json
uv run python -m scripts.benchmark_m3_excel --scenario benchmarks/scenarios/unique_cities.json
uv run python -m scripts.benchmark_m3_excel --rows 1000 --profile-memory
```

The first scenario uses repeated Berlin/Kyiv capitals; the second cycles
through four city/country pairs. The third command uses the default capitals
fixture and profiles peak Python allocations with `tracemalloc`.
`logical_operations` counts per-row DAG operations;
`executed_handler_calls` counts uncached handler invocations;
`reused_operations` counts memo hits; `transport_calls` counts fixture
requests (not HTTP traffic); `audit_operations` counts *deduplicated*
resolution reports; `audit_record_step_links` counts the row/step audit
references. A zero memo capacity disables operation-result reuse without
removing audit links. The current benchmark validates only disabled or
sufficiently provisioned memo capacities; intermediate capacities are not
a supported acceptance mode.

## Reproducible M2 smoke scenarios (developer tool)

These checks use the real `PlanValidator`, `JobExecutor`, provider adapters,
request policy and typed `Evidence`, without editing input workbooks.
They do **not** perform full Excel enrichment.

Run from the repository root after `uv sync --locked --group dev`:

```powershell
# Offline by default; deterministic fixture data, no API keys or network
uv run python scripts/smoke_live_providers.py --scenario capitals
uv run python scripts/smoke_live_providers.py --scenario mountains

# SQLite reuse across two independent jobs, using a disposable temporary DB
uv run python scripts/smoke_live_providers.py --scenario capitals --mode cache
uv run python scripts/smoke_live_providers.py --scenario mountains --mode cache

# Explicit, bounded external API traffic. Do not run these in CI.
uv run python scripts/smoke_live_providers.py --scenario capitals --mode live --allow-network
uv run python scripts/smoke_live_providers.py --scenario mountains --mode live --allow-network
```

For debugging live-source mismatches, enable a sanitized, bounded
provider response trace (public location/country/QID/class metadata only).
This does **not** increase HTTP request limits or loosen validation:

```powershell
uv run python scripts/smoke_live_providers.py --scenario capitals --mode live --allow-network --diagnose
uv run python scripts/smoke_live_providers.py --scenario mountains --mode live --allow-network --diagnose
```

The `source_trace` field shows Open-Meteo candidate names/feature codes and
Wikidata search candidates plus P31/P279 class references for requested
entities. It does not print HTTP headers, raw JSON payloads or API credentials.
Use this only for developer diagnosis; it is not a substitute for a domain
ambiguity policy and does not run in CI.

The live runs process **one** input record. Open-Meteo has a hard job budget
of 4 HTTP attempts; Wikidata has 16. Both are paced at 0.75 seconds between
requests and use **no automatic retries**. Live mode cannot start without
`--allow-network`. No OpenAI call is made. The JSON report includes per-step
typed outcomes, `Evidence`, provider request counters, job metrics and elapsed
time. A failed or partial record returns process exit code 1.

**Important:** The offline fixture provides a known demonstration value for
Mount Everest; the live Wikidata result can differ or fail because of current
source data, ambiguity or provider rate limits. Do not treat smoke fixtures as
independent ground-truth accuracy evaluation.

## Audited Resolution Foundation (M2)

**Implemented, opt-in (PRs #69–#74):** provider adapters retrieve and verify
normalized candidates, while the shared `ResolutionEngine` applies source-independent
decision semantics with provider-specific ranking strategies. `JobExecutor` can
connect `OpenMeteoResolutionService` and `WikidataMountainResolutionService`
through explicitly selected `resolution_services` and `resolution_policies`,
returning a typed `ResolutionJobReport` with each operation's alternatives,
reason codes, completeness and per-row links.

Under the default `strict` policy, incomplete candidate verification blocks
automatic mountain selection. The explicit opt-in `verified_only` policy can
select the sole positively verified eligible mountain even when other
alternatives remain unchecked; the audit retains its incomplete flags and
selection basis. This does not establish uniqueness. Prominence-based
auto-selection is separately opt-in and is never inferred from LLM output
or search position. The same candidate decision plus its evidence is cached
as a pair with the corresponding successful result, under policy/version/context-
specific keys. Strict legacy handlers still run when opt-in is absent.
See [Resolution Report Contract](10-resolution-report-contract.md).

**M3 update (PR #79):** Excel execution, workbook output, and validated
user-approved resume are implemented for audited Wikidata mountain
resolution. The resume workflow includes fresh source verification
and links to the original job.

General-purpose review workflows, grouped clarification questions,
selective dependent-step replay, and the web Review Workspace remain
future work. Live provider access requires explicit opt-in.

## Plan generation and evaluation

Inspect the workbook to identify the correct worksheet name:

```bash
uv run enrichment inspect samples/mountains.xlsx
```

Configure `OPENAI_API_KEY` and optionally `OPENAI_MODEL` in your
environment. When using a local `.env` file, pass `--env-file .env`
to `uv run`.

Generate a plan and export its dataset profile:

```bash
uv run --env-file .env enrichment plan generate samples/mountains.xlsx \
  --sheet Mountains \
  --instruction "Find the elevation of each mountain." \
  --output plan.json \
  --profile-output profile.json
```

Replace `Mountains` with the actual worksheet name if different.

Validate a saved or manually edited plan without calling an LLM:

```bash
uv run enrichment plan validate plan.json --profile profile.json
```

Evaluate structural validity and scenario correctness independently:

```bash
uv run enrichment plan evaluate plan.json \
  --profile profile.json \
  --scenario mountains
```

The evaluation CLI supports `mountains` and `capitals` as benchmark
scenarios. These evaluations assess planning correctness, not the
accuracy of enriched data values.

CLI exit codes:

- `0`: successful validation or evaluation.
- `1`: invalid plan or failed scenario evaluation.
- `2`: invalid input or technical failure.

## Repeated live planning experiments (developer tool)

Offline tests use scripted model responses, so they prove the planning loop but
not model quality. To measure how often a real model returns a valid plan, repeat
the planner on a sample task. This calls the OpenAI API with your key (up to three
completions per run), executes no providers and writes no workbook:

```powershell
uv run --env-file .env python -m scripts.experiment_planner --case capitals --model gpt-4.1-mini --repeat 10
uv run --env-file .env python -m scripts.experiment_planner --case mountains --model gpt-4o-mini --repeat 10 --capture-raw
```

Run it as a module (`-m`) from the repository root; `python scripts/experiment_planner.py`
fails with `No module named 'tests'`. `uv run` does not read `.env` unless
`--env-file .env` is passed.

- `--case` selects the standard `capitals` or `mountains` instruction and sample
  workbook; `--prompt-file` overrides the instruction, for example with a
  free-form or non-English request.
- `--output-targets distance` passes the target column names the way the UI does
  (they are shown to the model and enforced by validation). Without it the plan
  may name its targets freely, as in `enrichment plan generate`.
- `--repeat` accepts 1 to 10 runs per call; `--repairs` sets the repair budget (0 to 2).
- `--capture-raw` also saves the prompts and raw model responses per run. Use it
  only with the public samples and do not commit or share the files unreviewed.

Each call writes `summary.json` (model, git revision, attempts, diagnostic codes
and the final plan per run) to the ignored `artifacts/planner-experiments/`.
Compare models or code changes by the share of valid `ready` plans, not by a
single run.

## Live resolution diagnostics (developer tool)

`scripts/experiment_resolution.py` shows why each mountain name resolved, or
did not, against live Wikidata. It runs the production resolution service once
per name and then prints, for every candidate the source left unconfirmed, its
recorded type (`instance of`) and whether it has a country. No OpenAI key is
needed.

```powershell
uv run python -m scripts.experiment_resolution
uv run python -m scripts.experiment_resolution --policy verified_only
uv run python -m scripts.experiment_resolution --names "Annapurna/Nepal" "Annapurna I/Nepal"
```

The JSON report is written to the ignored `artifacts/resolution-experiments/`.
`--offline` uses the injected offline responses and only checks that the script
itself works. Results depend on live data and can change between runs.

## Scale experiment (developer tool)

One run is bounded for the public demo: 20 data rows, 400 requests per external
source, 300 seconds. `scripts/experiment_scale.py` measures what happens beyond
that. It builds a test workbook in the layout of a bundled sample, raises the
bounds for its own process and drives the same HTTP API the interface uses —
upload, the example's prepared plan, approval, one run, the report — against the
live sources. No OpenAI key is needed.

```powershell
uv run python -m scripts.experiment_scale --case capitals --rows 100
uv run python -m scripts.experiment_scale --case capitals --rows 500 --distinct 60
uv run python -m scripts.experiment_scale --case mountains --rows 50
uv run python -m scripts.experiment_scale --case capitals --rows 100 --demo-limits
```

It prints the run time, seconds and requests per row, the request counters of
each source (retries, rate-limit waits, refusals by the request budget), the
steps that produced no value, the size of the review and of the report, and
writes the same as JSON to the ignored `artifacts/scale-experiments/`.
`--demo-limits` raises only the row count, to show which remaining bound stops
a larger run first. `--offline` uses fixture answers and only checks the script.

Capitals rows are pairs, so `--distinct` sets how many different places are
looked up; mountains rows are one name each (about 140 in the list; `--repeat`
cycles it). Entries whose country name the application does not recognize are
left out unless `--keep-unrecognized` is given; with it, those rows stay empty
with the reason "country not recognized".

To look at a large table in the interface, build the workbook and start the
local server with raised bounds:

```powershell
uv run python -m scripts.scale_workbook --case capitals --rows 300
$env:ENRICHMENT_MAX_RUN_ROWS = "500"; $env:ENRICHMENT_MAX_TIMEOUT_SECONDS = "1800"
$env:ENRICHMENT_MAX_PROVIDER_REQUESTS = "20000"; $env:ENRICHMENT_MAX_PROVIDER_WAIT_SECONDS = "600"
uv run --locked --env-file .env python -m ai_data_enrichment.local_server --allow-network
```

These four variables (`ai_data_enrichment/run_limits.py`) exist for measurement.
The pipeline is not built for that scale: rows are processed one after another,
a run that is stopped leaves no partial result, and nothing is checkpointed.
Mind the sources too: Open-Meteo's free tier allows 10 000 calls a day per IP
address.

## Execute an Excel enrichment job (M3)

The `enrichment run` command executes a saved, validated plan
against an explicitly selected Excel table and writes the results
to a new workbook.

A reproducible Mountain Elevation example is available in
[demo/README.md](../demo/README.md).

The example demonstrates the Review → Approve → Resume workflow
using a previously reviewed Wikidata candidate.

Run from the repository root in PowerShell:

```powershell
New-Item -ItemType Directory -Path temp -Force | Out-Null

uv run --locked enrichment run demo/mountains_smoke.xlsx `
    --plan demo/mountains_plan.json `
    --sheet Mountains `
    --range A1:B2 `
    --columns-file demo/columns.json `
    --output temp/mountains_reproduced.xlsx `
    --allow-network `
    --max-rows 1 `
    --approvals-file demo/mountains_approvals.json `
    --source-report demo/mountains_enriched_audited.resolution.json
```

The command produces an enriched XLSX file and a corresponding
resolution audit report.

## Compare mountain resolution policies

Mountain resolution defaults to `strict`: every plausible alternative must be
verified or conclusively rejected before a unique match can be selected.
Opt in with `--mountain-resolution-policy verified_only` to select the sole
positively verified mountain in the requested country even when other candidates
remain unchecked, including after the 12-candidate verification budget is exhausted.
Unknown alternatives and the original incomplete-verification reason remain in
the audit. The selection uses `basis="verified_only"` and, when applicable,
`unchecked_alternatives_remain`, rather than claiming strict uniqueness. Two
eligible verified mountains require review. Neither policy accepts an incomplete
search, a failed provider request, or a candidate without positive verification.

Both modes use the same P31/P279 mountain verification, P17 country verification,
slash-separated country support, and bounded claim batches. There is no type
blacklist. `candidate_scan_complete` records whether every discovered candidate
was attempted; `verification_complete` remains false while candidates are unknown.
The verification budget (12 candidates that need provider lookups) and bounded
subclass traversal still apply.
Approvals continue to require fresh source verification and use `user_override`.

To compare both policies on the original 10-row Academic Mountains workbook,
run this PowerShell block from the repository root. It prepares one plan with
`expected_unit="m"` and writes to the existing empty `height` column C in two
new output workbooks. The source workbook and demonstration plan are preserved.
The two `enrichment run` commands explicitly enable live network access; these
are manual acceptance commands, not part of the offline test suite.

```powershell
$acceptanceDir = Join-Path "temp" ("mountains-policy-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Path $acceptanceDir -Force | Out-Null
$plan = Get-Content -LiteralPath "demo/mountains_plan.json" -Raw | ConvertFrom-Json
$plan.outputs = @(@{
    step_id = "elevation"
    value_field = "value"
    target_column = "height"
    expected_unit = "m"
})
[IO.File]::WriteAllText((Join-Path $acceptanceDir "plan.json"), ($plan | ConvertTo-Json -Depth 20))
[IO.File]::WriteAllText((Join-Path $acceptanceDir "columns.json"), '{"height":"C"}')

$mountainArgs = @(
    "samples/mountains.xlsx", "--plan", (Join-Path $acceptanceDir "plan.json"),
    "--sheet", "Sheet1", "--range", "A1:C11",
    "--columns-file", (Join-Path $acceptanceDir "columns.json"),
    "--max-rows", "10", "--allow-network"
)
uv run --locked enrichment run @mountainArgs --mountain-resolution-policy strict `
    --output (Join-Path $acceptanceDir "strict.xlsx")
uv run --locked enrichment run @mountainArgs --mountain-resolution-policy verified_only `
    --output (Join-Path $acceptanceDir "verified_only.xlsx")
```

Compare the resulting row metrics and the corresponding `strict.resolution.json`
and `verified_only.resolution.json` audits. Live coverage is not guaranteed;
unknown candidates can still block strict resolution, and search/provider limits
can prevent either mode from selecting a match. Under `verified_only`, an unchecked
alternative could be another eligible mountain; selection does not prove uniqueness.

**Important limitations:**

- External API access requires explicit `--allow-network`.
- The command processes up to 1,000 rows per job.
- Approval-based resume currently supports audited Wikidata
  mountain resolution.
- Approval manifests are bound to the original workbook and job.
- Output files must not already exist.
- Live provider responses may change or fail.
- This example demonstrates functionality, not independently
  measured factual accuracy.

The `temp/` directory is excluded from Git. Published demonstration
artifacts remain in `demo/`.
