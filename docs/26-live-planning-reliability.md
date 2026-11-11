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
is final on the first attempt; one that answers repair diagnostics is challenged
once (see "Abstention after repair feedback" below). There is no deterministic
override of a model's interpretation of free text.

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


## Optional parameters and explicit intent (#119)

An optional capability parameter may be omitted entirely: `allowed_values` constrains
supplied values, not presence. Ordinary literal-city geocoding uses `parameters: {}`; a
supplied `required_role: null` or an unsupported disable value remains invalid.
Country-capital queries still support `required_role: "country_capital"`. Parameters
that handlers demand are declared required (see "Required parameters" below).

Contract validation is unchanged. An additional deterministic intent policy checks
all matching capability steps using only the original user instruction. It recognizes
bounded English phrases: literal city/cities (including literal-city and city names),
not necessarily capitals, regardless of administrative role, do not require capital
status, and do not set required_role. Explicit global imperatives such as “Only accept
country capitals”, “Require country-capital status”, or “Set required_role=country_capital”
require the role; such a request
can qualify literal name lookup. An explicit prohibition and requirement together
remain conflicting and need clarification. Headers, samples and model assumptions
are never evidence for policy. These rules do not prove general semantic equivalence,
cover arbitrary languages, or resolve differently scoped instructions for individual
steps. Human review of the final proposal remains required. The small policy model
supports required/forbidden parameter values and can be extended with focused rules.

`INTENT_PARAMETER_CONFLICT` identifies each affected step/parameter. These errors
consume the existing repair budget (at most two repairs), without removing or coercing
any constraint. Every proposal is independently revalidated; abstention stays final,
including the observed false refusal about optional required_role. The API repeats
both checks for generated and supplied plans on creation, validation and approval;
the agent graph also preserves the instruction during revalidation.

Validation exposes `contract_valid` separately from overall `valid`; a contract-valid
intent conflict still blocks approval and running. The Plan UI explains the difference,
labels model assumptions as unverified, and explains optional restrictions and
execution-time provider evidence. Offline regressions do not establish improved model
quality. After review and merge, repeat the original natural-language task above from
the main Cursor folder, without internal parameter hints. No live-model acceptance
claim is made by these tests.


## Abstention after repair feedback

A recorded `gpt-4o-mini` run on the Capitals task (1 of 10, 2026-11-11) proposed a
correct DAG but named the geocode input `capital` after the column header. The
validator returned `MISSING_REQUIRED_INPUT`/`UNKNOWN_INPUT`; the model then returned
`unsupported` with a reason that restated the correction ("requires an input 'city'
instead of 'capital'"), and the planner accepted it as final. The earlier
`required_role` refusal recorded under #119 has the same shape.

The planner now distinguishes two situations:

- An abstention on the **first attempt**, or one **repeated after a challenge**, is
  final. Task support still cannot be proven from free text, and no plan is coerced.
- An abstention that **answers validation diagnostics** is challenged once. The
  earlier invalid proposal is replayed with its unchanged diagnostics, the model's
  abstention reason and a request to apply the corrections; it may abstain again
  only by naming the missing capability, source input or conflict.

The challenged attempt consumes budget and is recorded with the model's status and
the code `ABSTENTION_AFTER_FEEDBACK`. If no budget remains for the challenge, the
last parseable invalid DAG is returned with `validation.valid=false`, as for any
exhausted repair; it cannot be approved or run. The ordinary repair request no
longer suggests returning `plan: null`. Validation rules, the repair budget (at
most three completions) and the `PlanningResponse` schema are unchanged.

Offline regressions replay the recorded proposal and refusal; they prove the loop,
not model quality. Measure the effect with repeated live runs per model.


## Required parameters

The contract used to describe every parameter as optional, while three handlers
reject a step without their parameter: `geodesic_distance.unit`,
`resolve_entity.entity_type` and `lookup_property.property`. A plan with
`parameters: {}` for such a step passed validation, could be approved, and then
failed with `INVALID_PARAMETER` on every row.

`ParameterSpec` now carries `required` (default `false`). The three parameters above
are declared `required: true`; `geocode.required_role` stays optional. The validator
reports `MISSING_REQUIRED_PARAMETER` with the allowed values, as a contract error that
enters the ordinary repair loop. The planner sees the flag in the capability contracts
and the guidance no longer states that all parameters are optional.

There is no `default` field: no handler substitutes a value, so an omitted optional
parameter simply means the restriction is not applied. A unit test checks that every
parameter declared required is in fact rejected by its handler when omitted.


## Requested output targets

In the UI the user binds output names to Excel columns (`output_columns`, for example
`{"distance": "E"}`) before planning. The planner did not receive these names, and
the name check ran only after the repair loop, in the API. A plan that called the
target `Distance_km` was therefore reported as `invalid_output_mapping` with no
attempt to repair it. The standard sample instructions hid this because they name
the column literally.

`PlanValidationPolicy` now carries `output_targets`. The API builds one policy
(step limit plus the requested target names) and uses it both for planning and for
the final draft validation:

- the names are added to the model context as `output_targets`, with one rule in the
  system message: write each one exactly once and use no other target name;
- the validator reports `OUTPUT_TARGET_MISMATCH` with the missing and unexpected
  names, as a contract error that enters the repair loop;
- the step limit is likewise enforced inside the loop, not only afterwards.

Workbook-specific checks (column letters, overlap with the selected table, the A:CV
envelope) stay in the API as `invalid_output_mapping`: the model cannot repair them.
CLI planning passes no targets and is unchanged.
