# Live planning reliability (#117)

The [issue](https://github.com/ayri77/ai-data-enrichment-studio/issues/117) records
two genuine OpenAI attempts on `samples/capitals.xlsx`, `Sheet1!A1:E11`:

1. A refusal because geographic inputs were absent, despite registered producers
   being able to derive GeoPoints from the city/country columns.
2. After explicit composition guidance, a connected three-step DAG with
   `PARAMETER_TYPE_MISMATCH`: distance `parameters.unit` was an object.

The evidence does not identify the model ID or exact malformed parameter object.
The offline regression uses a representative literal-reference object; it does
not claim to replay an unavailable raw completion.

## Contract and recovery

Shared provider-independent guidance explains backwards capability composition,
intermediate typed `step_output` references, repeated capability use, and the
difference between input references and direct scalar parameters. It includes
an illustrative reference shape and `parameters={"unit":"km"}`, without a
scenario DAG or workbook column names. Before abstaining, the model is asked
to inventory compatible producers/consumers and identify the actual missing
contract, source input or clarification. A valid unsupported/ambiguous decision
is still final, including during repair. There is no deterministic override of
a model's interpretation of free text.

`LLMPlanner` allows one generation and up to two feedback repairs. Callers may
lower `max_repair_attempts` to 0 or 1; values outside 0–2 are rejected. Every
completed proposal consumes budget, including malformed JSON/schema. Schema
feedback includes error types/locations without Pydantic input/context dumps;
semantic feedback uses unchanged `PlanValidator` diagnostics, including stable
codes. Each candidate is parsed and independently validated again. No coercion
or automatic scalar unwrapping occurs. Only the latest proposal and feedback
are included with the original bounded context on the next call.

This follows M4's bounded propose/revalidate pattern and shares its prompt
guidance. The M4 graph/plan-only repair interface is not invoked: that graph
also executes jobs, whereas planning must remain separate from execution and
support malformed decision wrappers and explicit abstention. The existing M4
repair interface remains compatible.

Transport/authentication/completion failures stop immediately without another
planner call. The live adapter retains its 30-second SDK timeout, zero SDK
retries, 4096 output-token cap per completion and `store=False`. Thus the
planner makes at most three physical completion requests with the live adapter;
transport timeouts remain per request, not one overall wall-clock deadline.
Manually supplied plans are validated without invoking generation/repair.

## Inspectability and data boundaries

`PlanGenerationResult.attempts` and API drafts' `planning_attempts` contain only
attempt numbers, outcomes and diagnostic codes. These are also available in
the existing draft JSON/Diagnostics view. The final DAG and full independent
validation remain inspectable. If later repairs are malformed or unavailable,
the last parseable invalid DAG is retained with `validation.valid=false`; its
`ready` decision label does not enable approval. If no parseable candidate
exists, the API returns `planning_failed` with safe attempt count/codes.

Raw completions, schema input values, provider exception text and credentials
are not logged or stored as attempt diagnostics. A previous completion is
sent back only as repair context to the configured completion client. Credentials
stay in the existing provider configuration/SDK boundary and are never included
in planning prompts. Source data remains subject to the existing bounded
profile. Approval and execution independently revalidate the final plan and
retain source/fingerprint binding. Literal Istanbul and per-country context
remain intact; column labels do not impose country-capital roles. Ambiguous
row results still require review and cannot become invented source constants.

## Validation and genuine-model follow-up

Offline tests use the actual Capitals and Mountains workbook profiles and
free-text instructions with deterministic injected responses. They cover prompt
contracts, scalar mismatch feedback, malformed JSON/schema recovery, exhausted
budgets, invalid repaired references, final abstention, transport failures, and
approval gates. An HTTP-mocked real OpenAI SDK test covers repair request bounds,
token/storage settings and credential absence in prompts, logs and artifacts.
**Injected responses prove plumbing and safeguards, not genuine model planning
quality or that the first refusal can never recur.**

After CI/review and merge, repeat manual acceptance from the main Cursor folder:

1. Choose and record the nonsecret model ID in Provider Settings. Use the local
   key field; keep keys out of screenshots, logs, issue comments and test notes.
2. Upload `samples/capitals.xlsx`, select `Sheet1!A1:E11`, map `distance` to E,
   leave saved-plan JSON blank and generate using the original task:

   > Calculate the great-circle distance in kilometers between each pair of
   > cities in Capital_From and Capital_To. Use Country_From and Country_To to
   > disambiguate locations. Treat all locations as literal cities, including
   > Istanbul. Write verified distances into the distance output column. Leave
   > unresolved or ambiguous results empty; do not invent values.

3. Record model ID, instruction variant, planning status, attempt count/codes,
   final validation validity and DAG shape. A generated DAG is accepted only
   when `validation.valid=true`. Check two country-disambiguated geocodes feeding
   distance, scalar `unit="km"`, and no `required_role=country_capital`.
4. Repeat with explicit guidance: append “Compose two geocode steps, one per
   city/country pair, feeding their step_output GeoPoints into geodesic_distance
   with parameters.unit = km.” Record this separately from free-text acceptance.
5. Repeat the Mountains task with `Sheet1!A1:C11`, height mapped to C, and a
   request for verified elevations in metres. Check the resolve/lookup chain.
   Driving routes or insufficiently specified requests must still abstain.

The opt-in `tests/integration/test_openai_planner_live.py` cases mirror these
workbook profiles and both Capitals instruction variants. With an existing
secure environment configuration, `RUN_LIVE_LLM=1` enables them; `pytest -s`
prints the model ID, decision/validation status and safe attempt summaries.
No new genuine-model request was made for this change; manual acceptance is
intentionally deferred until after merge. Planning validation does not establish
factual correctness of later provider results.
