# Hosted demo

A public demo runs as **one process** that serves both the API and the built
frontend from one origin. It is the same application as the local launcher with
three differences:

| | Local launcher | Hosted demo |
| --- | --- | --- |
| Entry point | `python -m ai_data_enrichment.local_server` | `uvicorn --factory ai_data_enrichment.hosted:create_hosted_app` |
| Who can use it | Anyone on the machine | Only visitors who enter the access code |
| Provider key and model | Environment or the settings dialog | Environment only; the dialog is read-only |
| Frontend | Vite dev server on port 5173 | Built files served by the backend |

## Access codes

A visitor enters a code. There are two ways to set codes, and they can be used
together:

- `ENRICHMENT_ACCESS_CODES` — one code per visitor, as `label:code` entries
  separated by commas, semicolons or line breaks:
  `guest01:…,guest02:…`. A label is up to 40 letters, digits, `-` or `_` and is
  what the usage summary shows; a code is at least 8 ASCII characters without
  spaces, commas or semicolons. Labels and codes must not repeat.
- `ENRICHMENT_ACCESS_CODE` — one code, as before; its holder is counted under
  the label `shared`.

The hosted entry point **refuses to start without any code**, so a deployment
cannot be open by accident. A wrong entry is reported by its position, never
by its text.

Codes are made on your own machine:

```bash
uv run python -m scripts.make_access_codes --count 10 --admin
uv run python -m scripts.make_access_codes --count 5 --start 11   # more, later
```

The first block it prints is the value for `ENRICHMENT_ACCESS_CODES` (to add
visitors, append the new entries to the value you have, after a comma, and
redeploy); the second is the list to hand out. To take one visitor's access
away, remove that entry.

- Every `/api/*` route except `/api/health` and `/api/access` answers
  `401 access_required` without the code in the `X-Access-Code` header.
- The frontend shows a single field for the code, checks it with
  `GET /api/access`, keeps it for the browser tab (`sessionStorage`) and sends
  it with every request. The static files themselves are public; they contain
  no data.
- Wrong codes are answered after a short delay and counted: after 20 wrong
  attempts within a minute further wrong attempts get `429 access_throttled`.
  The correct code keeps working during that minute.
- A code protects your OpenAI budget and the uploaded workbooks of other
  visitors. Codes are not accounts: whoever has a code can use the demo, and
  all visitors share one workspace (see below). A code says who is counted,
  not what they can see.

## Usage summary

The server counts what is used, per code label: visits, examples opened,
uploads, plans (and failed planning), runs with rows and filled rows, downloads
of the filled table, requests to the AI with the tokens the provider reported,
and requests to each source. `usage.py` records **numbers and fixed words
only** — a model id, a capability id, a status — and refuses anything else, so
no table content, file name, task text, place name or network address of a
visitor can end up there. Identifiers of workbooks and jobs are not recorded
either.

- **Where to read it.** Set `ENRICHMENT_ADMIN_CODE` (at least 12 characters,
  different from every access code) and open `https://<the demo>/#usage`. The
  page asks for that code each time it is loaded and keeps it in memory only.
  It lists every label, used or not, the events by kind and the latest 300
  events. The code opens `GET /api/usage` and nothing else; a visitor's code
  does not open it; without the variable the route answers 404.
- **What survives a restart.** The summary is kept in memory, so a restart, a
  deploy or the free instance going to sleep clears it. Each event is also one
  line on standard output, `usage {"at": …, "who": "guest03", "kind":
  "run_finished", …}`, which Render keeps with the service's logs (7 days in a
  Hobby workspace, by Render's documentation of 2026-11-14). Search the logs
  for `usage {` to read what a restart took from the page.
- **What visitors are told.** The page that asks for the code says that use is
  counted per access code and that table contents, file names and task texts
  are not recorded.
- **What it is not.** Not web analytics: no page views, referrers, devices or
  locations, and nothing about someone who did not enter a code. A code handed
  to several people counts them as one.

## Environment

| Variable | Required | Meaning |
| --- | --- | --- |
| `ENRICHMENT_ACCESS_CODES` | one of the two | One code per visitor, `label:code` entries |
| `ENRICHMENT_ACCESS_CODE` | one of the two | One code, counted as `shared` |
| `ENRICHMENT_ADMIN_CODE` | no | Opens the usage summary at `/#usage`; at least 12 characters |
| `OPENAI_API_KEY` | for live planning | Used only on the server |
| `OPENAI_MODEL` | no | A model of the catalog (`planning/model_catalog.py`): `gpt-6-luna` (the default when unset, and what `render.yaml` sets) and `gpt-4.1-mini` were measured for planning; `gpt-4.1` was not. Measure a model before serving it: [live planning reliability](26-live-planning-reliability.md) |
| `OPENROUTESERVICE_API_KEY` | no | Offers the driving distance by road; one account's daily quota is shared by all visitors. See [road distance and ratio](30-road-distance-and-ratio.md) |
| `ENRICHMENT_ALLOW_NETWORK` | for live runs | `1` opens the server network gate; each run still needs the visitor's own consent |
| `ENRICHMENT_STATIC_DIR` | no | Built frontend, default `frontend/dist` (set in the image) |
| `PORT` | no | Listening port, default `10000` in the image |

Do not set the `ENRICHMENT_MAX_*` variables on a hosted demo. They raise the
bounds of one run for local measurement (see the scale experiment in [command-line and developer tools](28-cli-and-developer-tools.md));
the defaults — 20 rows, 400 requests per source, 300 seconds — are what keeps a
shared instance short, cheap and polite to the free external sources.

## Examples

A visitor of a public demo usually has no suitable workbook at hand. While no
workbook is loaded, the Dataset screen offers the bundled examples (Capitals and
Mountains). One click downloads the example from the server, sends it through
the **ordinary upload and preview**, and fills in the measured instruction and
the output column. External-request consent stays unticked: that remains the
visitor's decision.

Each example also has a prepared plan. **Use it instead of asking the AI** puts
that plan into the saved-plan field, so the demo continues when the planning
provider is slow, unavailable or out of budget. The plan is validated like any
supplied plan; execution still contacts the data sources.

Routes (behind the access code like every other API route): `GET /api/samples`,
`GET /api/samples/{id}/workbook`, `GET /api/samples/{id}/plan`. The files come
from `samples/` and `demo/` under the working directory (`ENRICHMENT_DATA_DIR`
overrides it); an example whose workbook is missing is not offered. The same
buttons appear in the local launcher when it is started from the repository root.

## What a hosted demo does not have

- **No persistence.** Uploads, plans, jobs and results live in memory and in a
  temporary directory. A restart, a new deploy or a platform sleep/wake cycle
  clears them. Visitors should download results they want to keep.
- **One process, one running job.** A second visitor who starts a run while
  another is running gets `job_busy`.
- **Shared limits.** At most 8 workbooks, 32 plans and 16 jobs are kept for all
  visitors together; the oldest finished ones give way
  ([plan/run API](22-m5-plan-run-api.md), retention).
- **No per-user separation.** Identifiers are random and never listed, but
  everyone shares the same process.

## Run the image locally

```powershell
docker build -t enrichment-studio .
docker run --rm -p 10000:10000 `
    -e ENRICHMENT_ACCESS_CODE=choose-a-code `
    --env-file .env -e OPENAI_MODEL=gpt-6-luna -e ENRICHMENT_ALLOW_NETWORK=1 `
    enrichment-studio
```

Open http://127.0.0.1:10000 and enter the code.

Without Docker, the same process can be started from a checkout:

```powershell
cd frontend; npm ci; npm run build; cd ..
$env:ENRICHMENT_ACCESS_CODE = "choose-a-code"; $env:ENRICHMENT_ALLOW_NETWORK = "1"
uv run --locked --env-file .env uvicorn --factory ai_data_enrichment.hosted:create_hosted_app --port 10000
```

## Deploy on Render

`render.yaml` in the repository root describes one Docker web service.

1. Push the branch that contains `Dockerfile` and `render.yaml` to GitHub.
2. In the Render Dashboard choose **New → Blueprint** and connect the repository.
3. Render reads `render.yaml` and asks for the values marked `sync: false`:
   the access codes, the admin code, `OPENAI_API_KEY` and the optional routing
   key. Enter them there; they are not stored in the repository. A variable
   left empty is as if it were not set.
4. Apply. The first build takes several minutes (Node build, then Python
   dependencies).
5. Open the service URL, enter the access code, and run one of the samples.

Notes for the free instance type (check Render's current terms before a demo):

- It sleeps after a period without traffic and takes about a minute to wake;
  open the link shortly before presenting. Sleeping also clears all state.
- `autoDeploy` is off in the blueprint, so a merge to `main` does not restart
  the demo in the middle of a presentation. Deploy manually from the dashboard.
- Set a monthly spending limit in the OpenAI account as a second safeguard.

## Verification status

The access control, the static serving and the entry point are covered by
`tests/integration/test_hosted_access.py` and `frontend/src/AccessBoundary.test.tsx`,
and the built frontend was exercised through the hosted entry point in a real
browser. The Docker image build and the deployment on Render itself have not
been run from this repository's CI.
