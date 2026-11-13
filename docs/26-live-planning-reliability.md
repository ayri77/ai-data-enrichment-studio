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
   (Since `road_distance` was added, a driving distance must be refused only
   where no routing key is configured; see
   [road distance and ratio](30-road-distance-and-ratio.md).)

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


## Supported planning models (measured 2026-11-11 and 2026-11-13)

Repeated live runs with `scripts/experiment_planner.py`, standard sample instructions,
10 runs per row, at most two repairs. "Before" is commit `0b515aa` or earlier, "after"
is `bf4f999` (required parameters and requested output targets merged).

| Task | Model | Valid plans | Valid on first attempt |
| --- | --- | --- | --- |
| Capitals | `gpt-4.1-mini` | 10 of 10 (before) | 10 of 10 |
| Capitals | `gpt-4o-mini` | 9 of 10 (before), 10 of 10 (after) | 9 of 10, 7 of 10 |
| Mountains | `gpt-4.1-mini` | 10 of 10 (after) | 10 of 10 |
| Mountains | `gpt-4o-mini` | 1 of 10 (before), 0 of 10 (after) | 0 of 10, 0 of 10 |
| Capitals | `gpt-6-luna`, effort `low` | 10 of 10 (2026-11-13) | 10 of 10 |
| Mountains | `gpt-6-luna`, effort `low` | 10 of 10 (2026-11-13) | 10 of 10 |

On Mountains `gpt-4o-mini` returns `unsupported` on the first attempt ("no capability
to directly resolve mountain elevations"): it does not compose `resolve_entity` with
`lookup_property`. The result predates the contract changes, so it is not a regression.
A first-attempt abstention is final by design, so the repair loop does not help.

`gpt-4o-mini` is therefore removed from the model catalog (`planning/model_catalog.py`), and `gpt-4.1-mini` became the
default. `gpt-6-luna` met the same bar on 2026-11-13 (section below); since then it is the
default when `OPENAI_MODEL` is unset and what the hosted demo runs. `gpt-4.1` remains selectable but has not been
measured. Free-form and non-English instructions have not been measured on any model.
The samples are small; treat the table as a floor for decisions, not as a rate.


## The Luna tier: `gpt-6-luna` measured on 2026-11-13

Asked on 2026-11-12: why not OpenAI's "Luna", which is cheaper and scores higher on
public benchmarks. Two models carry the name. The Luna rows are from OpenAI's model pages
on that day, the `gpt-4.1` prices from public price comparisons. `gpt-6-luna` has since
been run against this project (result below); `gpt-5.6-luna` has not.

| Model | Input / output per 1M tokens | Released | Kind |
| --- | --- | --- | --- |
| `gpt-4.1-mini` (default at the time) | $0.40 / $1.60 | 2025-04 | no reasoning |
| `gpt-4.1` | $2.00 / $8.00 | 2025-04 | no reasoning |
| `gpt-5.6-luna` | $0.20 / $1.20 | 2026-07 | reasoning, default effort `medium` |
| `gpt-6-luna` | $0.10 / $0.50 | 2026-09 | reasoning, default effort `medium` |

What followed from the facts before the measurement, kept as the reasoning behind it:

- **Price is not the argument here.** One planning request for the samples is about
  3,100 input tokens (prompt, capability contracts, table profile and schema, estimated
  at four characters per token) and a few hundred output tokens: about a fifth of a
  cent on `gpt-4.1-mini`. A cheaper model saves fractions of a cent per plan, less
  whatever its reasoning tokens cost.
- **Benchmarks are not the argument either.** `gpt-4o-mini` was a capable model and
  produced 0 valid Mountains plans in 10 (table above). What decides is this planner's
  own contract, measured.
- **Both are reasoning models, and this adapter was written for models without
  reasoning.** Hidden reasoning tokens are billed as output and count against
  `max_output_tokens` (4096 in the server); a response that spends the budget on
  reasoning comes back `incomplete`, which the adapter treats as a failure. The server
  waits 30 s for one request.
- A report on OpenAI's developer forum describes `gpt-6-luna` occasionally returning an
  extra message item for structured-output requests. Unverified here; if it happens
  with this adapter's JSON mode, it would show up as malformed JSON and a repair.

So the model was not added to the list on the strength of the question. (Since 2026-11-13 it can be chosen, marked as not measured; see "Where models are described" below.) The
adapter can now state an effort (`OpenAIPlanClient(reasoning_effort=...)`, sent only when
set), and the measurement script takes `--reasoning-effort` and `--max-output-tokens`
and reports how long each request took and how many exceeded the server's 30 s.

To measure (about 40 requests per model and effort; a few cents in total):

```
uv run --env-file .env python -m scripts.experiment_planner --case capitals --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets distance
uv run --env-file .env python -m scripts.experiment_planner --case mountains --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets height
```

and the same two with `--reasoning-effort none`. Acceptance, the same bar the default
model met: 10 of 10 valid plans on both tasks, no request over 30 s, no `incomplete`
responses.

### Result (2026-11-13, effort `low`)

Run by the project owner with the two commands above, at the script's default output
limit of 4,096 tokens.

| Task | Valid plans | On the first attempt | Median request | Slowest request | Over 30 s |
| --- | --- | --- | --- | --- | --- |
| Capitals | 10 of 10 | 10 of 10 | 4.13 s | 9.95 s | 0 |
| Mountains | 10 of 10 | 10 of 10 | 3.02 s | 4.05 s | 0 |

No request needed a repair, so none came back `incomplete` or malformed; the extra
message item reported on the forum did not show in these 20 requests. The catalog entry
is therefore `measured=True` with effort `low`, and `render.yaml` names the model for the
hosted demo. By the owner's decision of the same day it is also the default for a process
without `OPENAI_MODEL`: both measured models met the same bar, and it is the cheaper by
list price. `gpt-4.1-mini` stays in the catalog as the measured alternative without
reasoning.

What this does not cover: effort `none` and the other efforts; what a plan costs in
practice, since the script does not record token usage; the two other requests the
application sends to the model (other names for a place, tasks for a table), which have
not been measured on any model; instructions other than the two standard ones.

### Where models are described

`src/ai_data_enrichment/planning/model_catalog.py` is the one place. Each `ModelSpec` has
the model's id, whether its planning reliability was measured, and for a reasoning model
the effort sent with every request and a reserve of output tokens for hidden reasoning,
added to what the visible answer needs. The web workspace offers and accepts exactly the
catalog (`provider_settings.py`), the server and the command line build their client
from it (`model_client`), and the default must be a measured entry. Adding a model is one
entry; nothing else in the application names a model. Tests, this document and
`render.yaml` still do.

`gpt-6-luna` is in the catalog with the measured effort `low` and a reserve of 4096
tokens for reasoning; the reserve is headroom, since the measurement passed within 4,096
tokens in total. `gpt-4.1` is there unmeasured, and the interface shows "(not measured)"
beside such a model. The measurement script
keeps its own `--reasoning-effort` and `--max-output-tokens` flags, because it exists to
try values the catalog does not have yet.
