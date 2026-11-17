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

and the same two with `--reasoning-effort none`.

The "fill the gaps" example (`mountains_gaps`: the Mountains table with four heights already filled in, asked to fill the missing ones and keep the others) is a separate case. It has not been measured. It uses the same plan as Mountains, so the question is whether the planner accepts the wording and returns that plan:

```
uv run --env-file .env python -m scripts.experiment_planner --case mountains_gaps --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets height
```

Latitude and longitude of a city in two new columns of the Capitals table (`capitals_coordinates`, one `geocode` step with two outputs, which is not an example of the demo) is a case of its own (measured on 2026-11-15, see "Field outputs of `geocode`"):

```
uv run --env-file .env python -m scripts.experiment_planner --case capitals_coordinates --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets latitude,longitude
```

The population of a city in one new column of the Capitals table (`capitals_population`, one `geocode` step with one output; also not an example of the demo) is the case for the facts of a found place (measured on 2026-11-15, see "Field outputs of `geocode`"):

```
uv run --env-file .env python -m scripts.experiment_planner --case capitals_population --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets population
```

**After the changes that declared the fields of `geocode`'s output (`latitude` and `longitude`, then `population` and `timezone`), all planning cases must be measured again, not only these.** Every plan request carries the capability contracts, so every request now carries the changed `geocode` contract (and its new version, 1.2); the results above were measured on the earlier ones and do not cover them.
 Acceptance, the same bar the default
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

### Six capabilities (2026-11-14): abstentions over "leave empty"

After `ratio` and `road_distance` were added
([road distance and ratio](30-road-distance-and-ratio.md)), the owner repeated the
measurement with a routing key, so the planner was offered six capabilities
(`gpt-6-luna`, effort `low`, commit `25e0ee7`).

**Evidence.**

| Task | Valid plans | Abstained | Median request | Slowest request | Over 30 s |
| --- | --- | --- | --- | --- | --- |
| Capitals | 10 of 10 | 0 | 4.56 s | 5.60 s | 0 |
| Mountains | 8 of 10 | 2 | 3.24 s | 5.41 s | 0 |
| Capitals with roads, three columns | 9 of 10 | 1 | 5.63 s | 8.46 s | 0 |

The three failures are abstentions on the first answer (`unsupported`), not invalid
plans, and all three give the same reason (`summary.json` of the two runs): the
instruction ends with "Leave unresolved or ambiguous results empty; do not invent
values", and the model found no capability "to convert those failures into empty
height values". One of them quotes the guidance itself: failures "remain execution-time
failures for review, so the requested empty-result behavior is unsupported".

**Inference.**

- The cause is a gap in what the planner is told, not a missing capability. The guidance
  said that unresolved values stay failures for review; it did not say that their cells
  stay empty. Execution has always done exactly that: a step without a verified value
  writes nothing for the row.
- Whether the two new capabilities made this more frequent is not shown. The sentence
  that was misread is older than they are, the Mountains task uses neither of them, and
  2 of 10 against the 0 of 10 measured the day before with four capabilities is within
  what ten trials can differ by chance. The released four-capability set may have the
  same weakness at a lower rate.
- A visitor who meets it sees a refusal with that explanation and can ask again; nothing
  wrong is written.

**Change.** `PLAN_CONTRACT_GUIDANCE` (`planning/instructions.py`) now states that
execution leaves the destination cells of such a row empty, and that a request to leave
unresolved, ambiguous or missing results empty, or not to invent values, needs no step,
parameter or capability and is never a reason to abstain. Validation is unchanged: this
tells the planner a fact about execution and accepts no plan that was refused before.

**After the change** (owner, 2026-11-14, the same model, effort and six capabilities):

| Task | Valid plans | Abstained | Median request | Slowest request | Over 30 s |
| --- | --- | --- | --- | --- | --- |
| Capitals | 10 of 10 | 0 | 4.25 s | 5.73 s | 0 |
| Mountains | 10 of 10 | 0 | 1.86 s | 2.91 s | 0 |
| Capitals with roads, three columns | 10 of 10 | 0 | 5.53 s | 10.63 s | 0 |

All thirty plans came on the first answer. Thirty requests without an abstention after
three in thirty before are consistent with the change having worked; they do not put a
number on how rare an abstention now is.

**Not measured:** the set of five capabilities that an installation without a routing
key offers. A hosted demo should therefore either be given the routing key, so that it
offers the measured six, or be measured without the key first.

### Field outputs of `geocode` (2026-11-15)

Two changes on this day altered the contract of `geocode` that every plan request carries: its output declared `latitude` and `longitude` (version 1.1), then `population` and `timezone` with a longer description (version 1.2). The owner measured after each, `gpt-6-luna` at effort `low`, six capabilities offered, ten requests per case, no repair attempts in any run.

| Case | Output targets | After latitude/longitude (on `main`) | After population/timezone (branch `feat/city-facts`) |
| --- | --- | --- | --- |
| `capitals` | default | 10 / 10, median 4.05 s | 10 / 10 |
| `mountains` | default | 10 / 10, median 3.17 s | 10 / 10 |
| `capitals_roads` | `distance,road,detour` | 10 / 10, median 5.55 s | 10 / 10 |
| `mountains_gaps` | `height` | 10 / 10, median 2.33 s | 10 / 10 |
| `capitals_coordinates` | `latitude,longitude` | 10 / 10, median 2.11 s | 10 / 10 |
| `capitals_population` | `population` | — (case added later) | 10 / 10 |

The first column is from the script's output; for the second the owner reported the counts, and the timings were not recorded here. Before both changes `mountains_gaps` had also given 10 / 10 (the instruction that asks to fill only the missing values and keep the others).

What this shows and what it does not: the planner returns a valid plan for these six tasks with the changed contract, without guidance about fields. The script counts valid plans; it does not compare a plan with an expected one, so it would not notice a valid plan that takes a different route. That is why the elevation of a place record is not offered as a field (it would be a second, shorter route to "elevation of a mountain").

### Route check

Every number above counts **valid** plans: plans that pass the validator. A valid plan can still
be wrong in substance, and the validator cannot see it, because it knows capabilities and
types, not the task. Examples: a mountain's height read from a place record instead of the
elevation of a resolved mountain; the right kind of number written into the wrong column; a
step that looks up an entity literally named `fake_age_entity` and writes its elevation into a
column called "age" (seen live on 2026-11-16 with another model).

`scripts/experiment_planner.py` therefore also compares each valid plan with the **expected
route** of its case, stated as data in `scripts/planner_routes.py`: for every output column,
which capability and field must produce it, the parameters that give it its meaning
(`property = elevation`, `entity_type = mountain`, the unit, and no `required_role` where
the instruction says "literal cities"; for `capitals_coordinates` and `capitals_population`, whose
instruction only says "the city", `required_role` may be absent or `country_capital`, and when
it is set the run notes `OPTIONAL_PARAMETER_SET` without failing; any other value deviates), where each input must come from (a named table column, or the result of a step of a
given capability, checked the same way down the chain; a literal is never accepted as an input),
the unit on the output mapping, and the capabilities a plan may use at all. A step that feeds no
output is reported (`UNUSED_STEP`) and does not fail the route. Each deviation has a code
(`WRONG_CAPABILITY`, `WRONG_FIELD`, `WRONG_PARAMETER`, `LITERAL_INPUT`, `WRONG_SOURCE_COLUMN`,
`WRONG_UPSTREAM`, `MISSING_INPUT`, `UNEXPECTED_INPUT`, `UNEXPECTED_CAPABILITY`,
`MISSING_TARGET`, `EXTRA_TARGET`, `WRONG_UNIT`; `UNUSED_STEP` and `OPTIONAL_PARAMETER_SET` are
notes, not failures) and a sentence naming steps, targets and headers,
never a cell value or the value of a constant. The expectations of the four examples are
checked against their prepared plans by a test, so they cannot drift from the known-good routes;
`capitals_coordinates` and `capitals_population` have no prepared plan and were written by hand.

The run prints `route: expected` or `route: deviates (CODE, ...)` after each run, and the
summary adds the valid plans on the expected route, the valid plans that deviate (with a count
per code) and the headline from now on: **"N of M: valid and on the expected route"**.
`summary.json` carries the same numbers and, per run, the verdict and the deviations. The exit
code is unchanged. Invalid plans and refusals are counted as before and are not judged.

**What it cannot judge.** It knows the expected route of the six measured cases and nothing
else: it says nothing about any other table, instruction or task, and a plan outside them can
only be read by a person. It does not run the plan or check any result. It cannot tell a
different but equally correct route from a wrong one: the expectation is the known-good one (two
geodesic ends may be swapped; the order of steps and their ids do not matter). It trusts the
`valid` flag the run recorded and does not validate again.

**Status of the recorded results.** Every result in this document counted validity only.
**None of them has a route verdict yet**, including the 10 of 10 rows of 2026-11-13 to
2026-11-15; a plan among them may have been valid and off the route. Plans were not saved as
files before this section, but `summary.json` of each run holds them, so a past measurement
whose directory still exists can be judged offline (below). Otherwise the baseline is measured
again.

**What a saved plan can hold.** A plan is structure: column headers, capability names,
parameters, units. It can also hold cell values: through a literal input (the model copies a
cell into a constant, which is what `LITERAL_INPUT` reports), through a parameter, and through
its free-text `assumptions` (the prepared Capitals plans themselves name "Istanbul" there), and
`summary.json` also holds the model's `reason`. Treat `artifacts/planner-experiments/` as data,
not only as structure; it is ignored by git. The script warns when a run contains a literal
input.

To measure the baseline (the planner as it is today, `gpt-6-luna` at the measured effort `low`,
six cases, 10 runs each; PowerShell, from the repository root; `capitals_roads` needs the routing
key in `.env`):

```
uv run --env-file .env python -m scripts.experiment_planner --case capitals --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets distance
uv run --env-file .env python -m scripts.experiment_planner --case mountains --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets height
uv run --env-file .env python -m scripts.experiment_planner --case capitals_roads --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets distance,road,detour
uv run --env-file .env python -m scripts.experiment_planner --case mountains_gaps --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets height
uv run --env-file .env python -m scripts.experiment_planner --case capitals_coordinates --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets latitude,longitude
uv run --env-file .env python -m scripts.experiment_planner --case capitals_population --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets population
```

(The script has no default model of its own: `--model` or `OPENAI_MODEL` is required, and the
application's default is `gpt-6-luna`.) To judge the plans of an earlier run directory without
calling the model (no key and no network; it writes `routes.json` beside `summary.json` and
leaves `summary.json` as it was):

```
uv run python -m scripts.experiment_planner --routes-only artifacts/planner-experiments/20261115T101500Z-ab12cd34
```

Record the results as "N of 10: valid and on the expected route", with the deviation codes of
the others, next to the validity count. The route check is the instrument for the planned change
that lets the planner hand columns it cannot fill to a web search: measure the baseline first,
then again after the change, and look at whether columns it can fill are being handed away
(`MISSING_TARGET`) or reached by shortcuts (`WRONG_CAPABILITY`, `LITERAL_INPUT`).

#### Baseline with a route verdict (2026-11-17, by the owner)

`gpt-6-luna`, effort `low`, six capabilities offered, ten runs per case, at revision
`1dd7bf6`. This is the first measurement that judges the route.

| Case | Valid | Valid and on the expected route | Deviating | Refused |
| --- | --- | --- | --- | --- |
| `capitals` | 10 | 10 | 0 | 0 |
| `mountains` | 10 | 10 | 0 | 0 |
| `capitals_roads` | 10 | 10 | 0 | 0 |
| `mountains_gaps` | 9 | 9 | 0 | 1 |
| `capitals_coordinates` | 10 | 10 | 0 | 0 |
| `capitals_population` | 10 | 10 | 0 | 0 |
| **Total** | **59 of 60** | **59 of 60** | **0** | **1** |

- **No valid plan left the expected route**, and none carried a note. Every plan that
  passed validation used the capabilities, fields, parameters and columns the case expects.
- **One refusal on `mountains_gaps`.** The planner's reason: the capabilities can resolve
  each mountain and read its elevation, "but none provides a way to update only missing
  height cells while preserving existing values. Mapping results to height could overwrite
  values already present." The planner is not told that a run keeps the cells of an output
  column that already hold a value. This is the same kind of gap as the one closed on
  2026-11-14 for "leave unresolved results empty": a fact about execution that the
  instruction does not state. The earlier measurement of this case was 10 of 10 (2026-11-15);
  one refusal in twenty runs so far.
- Requests took 2 to 6 s; none came near the server's 30 s limit.

The fix for the refusal is a sentence in the planner's instruction, which changes what the
planner is shown. It goes in together with the next change to the planner (naming the columns
it cannot fill), so that one new measurement covers both.

### Naming the columns it cannot fill (not measured)

**None of this has been measured.** The change is made and tested offline with scripted
answers only; no model has seen the new texts. Every number in this document above this
section describes the planner without them.

**What changed.** Until now a plan had to write exactly the output columns the user chose
(`OUTPUT_TARGET_MISMATCH`). With five chosen columns and a source for four, the planner had to
refuse the task or return a plan for four, which failed validation, and nothing verified was
filled. The planner may now answer `ready` with a plan for the columns it can fill and name the
others in a new field of its answer, `unfilled_targets` (a list of column names, empty by
default, only allowed with `ready`). The server then offers a runnable plan for the covered
columns and says which columns have no source. The planner is told nothing about what happens to
the listed columns, and nothing in what it is shown mentions a web search.

**What the planner is now told**, verbatim. In the system text of `LLMPlanner.generate`, the
sentence about `output_targets` changed from "write each one exactly once as an output
target_column and use no other target name." to:

> write each one exactly once as an output target_column, except the ones you list in
> unfilled_targets, and use no other target name.

and directly after it:

> If the capabilities can produce what some of the output_targets ask for but not what others
> ask for, answer status 'ready' with a plan that writes the targets it can, and list the names
> of the others in unfilled_targets. A target belongs in unfilled_targets only when no
> capability can produce what that column asks for: never to shorten a plan, never because a
> value might be missing for some rows, and never when a capability can produce it. Each target
> is either written by the plan or listed in unfilled_targets, not both. Do not write a
> substitute value into a listed column and do not build steps for it. When no target can be
> produced at all, answer 'unsupported'. Otherwise unfilled_targets is an empty list.

In `PLAN_CONTRACT_GUIDANCE` (shared with the repair stage of the M4 agent, which is why this
sentence says nothing about unfilled targets), after "... is never a reason to abstain.":

> Cells of a target column that already hold a value are kept by the run unless the user chose
> to replace them; the plan maps a step's result to the column and needs no step or capability
> for that.

In `_ABSTENTION_CHALLENGE_REQUEST`, one phrase: "can produce the requested output" became "can
produce any of the requested output", so that the challenge does not push a model that can fill
part of the task to refuse all of it. `_REPAIR_REQUEST` was read and is unchanged: it does not
contradict the new rule. The response schema gains the property `unfilled_targets` (an array of
strings, not required). The capability descriptions are unchanged.

**Why.** Two pieces of evidence above. (1) The baseline refusal on `mountains_gaps`: the planner
did not know that a run keeps cells that already hold a value, and refused on that ground; the
second sentence states the fact, the same kind of fix as "leave unresolved results empty" on
2026-11-14. (2) The six-capabilities measurement: the planner follows the letter of its
instruction, and a rule that pushes it to produce "something" makes weaker models invent. So the
new rule says when a column may be listed (only when no capability can produce it), forbids the
three shortcuts, forbids a substitute value, and keeps `unsupported` for the case where nothing
can be produced. How it follows the rule is exactly what is not measured. Known risk: the
unchanged sentence "If no supported chain exists or intent is insufficiently specified, abstain"
still reads as a reason to refuse a task that can be done in part; the new rule is more specific
and comes first in the text, but whether the model weighs it that way is for the measurement.

**Validation.** The columns a plan must write are the requested ones minus the unfilled ones,
computed in one function, `apply_unfilled_targets` in `validation/plan_validator.py`, which the
planner loop and the server's validation both call. `PlanValidator` itself keeps its one rule
and does not know about unfilled targets. A name that is not a requested target
(`UNKNOWN_UNFILLED_TARGET`), a name the plan also writes (`UNFILLED_TARGET_ALSO_WRITTEN`), and
unfilled names in a request without output targets (`UNFILLED_TARGETS_WITHOUT_TARGETS`) are
diagnostics that go back to the model through the repair loop; such a name reduces nothing, so
a missing column is still reported. Every other check applies to the plan as before.

**What the measurement must show before this is merged.** At `gpt-6-luna`, effort `low`, ten
runs per case, with the output targets the interface sends:

1. The six existing cases stay valid and on the expected route (compare with the baseline
   above: 59 of 60), and **no target is handed away**: the count "Valid plans that handed away a
   target the case expects to be filled" is 0 in every one of them (`TARGET_HANDED_AWAY` is a
   blocking deviation).
2. `mountains_gaps` loses its refusal, or at least does not get worse (baseline: 9 valid, 1
   refused).
3. The two new cases, `mountains_age` (targets `height,age`) and `capitals_mayor` (targets
   `distance,mayor`), hand away exactly the expected column (`age`, `mayor`) and write the other
   by its route; a plan that writes the column nothing can fill is `TARGET_NOT_HANDED_AWAY`, and
   one that hands away the other column is `TARGET_HANDED_AWAY`.
4. No plan that invents (a literal input, a made-up entity, a substitute value) and no new
   refusal on the six existing cases.

The eight commands (PowerShell, from the repository root; `capitals_roads` needs the routing key
in the environment):

```powershell
uv run --env-file .env python -m scripts.experiment_planner --case capitals --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets distance
uv run --env-file .env python -m scripts.experiment_planner --case mountains --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets height
uv run --env-file .env python -m scripts.experiment_planner --case capitals_roads --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets distance,road,detour
uv run --env-file .env python -m scripts.experiment_planner --case mountains_gaps --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets height
uv run --env-file .env python -m scripts.experiment_planner --case capitals_coordinates --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets latitude,longitude
uv run --env-file .env python -m scripts.experiment_planner --case capitals_population --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets population
uv run --env-file .env python -m scripts.experiment_planner --case mountains_age --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets height,age
uv run --env-file .env python -m scripts.experiment_planner --case capitals_mayor --model gpt-6-luna --reasoning-effort low --repeat 10 --output-targets distance,mayor
```

The baseline above was taken with the same `--output-targets` as these lines, for all six
cases, so the two measurements differ only in what the planner is shown.

**The new cases.** `mountains_age` asks for the height of each mountain and "the geological age
of the mountain in millions of years, into a new column named age"; `capitals_mayor` asks for
the great-circle distance and "the name of the current mayor of the city in Capital_From, into
a new column named mayor". No capability knows either. The script records the unfilled targets
of each run and counts the valid plans that handed away a target the case expects to be filled
(`valid_handed_away_expected_filled` in `summary.json`).

**Not connected to the search.** Nothing in this step offers or starts the universal search for
the listed columns; the search is offered exactly as before (`fallbackOffer`, which gives
nothing for a runnable plan).

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
