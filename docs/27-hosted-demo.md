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

## Access code

`ENRICHMENT_ACCESS_CODE` is a shared secret of at least 8 ASCII characters
without spaces. The hosted entry point **refuses to start without it**, so a
deployment cannot be open by accident.

- Every `/api/*` route except `/api/health` and `/api/access` answers
  `401 access_required` without the code in the `X-Access-Code` header.
- The frontend shows a single field for the code, checks it with
  `GET /api/access`, keeps it for the browser tab (`sessionStorage`) and sends
  it with every request. The static files themselves are public; they contain
  no data.
- Wrong codes are answered after a short delay and counted: after 20 wrong
  attempts within a minute further wrong attempts get `429 access_throttled`.
  The correct code keeps working during that minute.
- The code protects your OpenAI budget and the uploaded workbooks of other
  visitors. It is one code for everyone, not per-user accounts: anyone who has
  it can use the demo. Change it in the environment to revoke access.

## Environment

| Variable | Required | Meaning |
| --- | --- | --- |
| `ENRICHMENT_ACCESS_CODE` | yes | Shared access code |
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
3. Render reads `render.yaml` and asks for the two values marked `sync: false`:
   `ENRICHMENT_ACCESS_CODE` and `OPENAI_API_KEY`. Enter them there; they are not
   stored in the repository.
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
