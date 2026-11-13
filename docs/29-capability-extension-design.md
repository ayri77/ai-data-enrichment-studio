# Adding a capability: where one is declared today, and a proposed contract

**Status:** design note for discussion, written after release 0.1.0. Nothing here
is implemented. It separates what the code shows (evidence), what follows from
it (inference) and what is proposed (recommendation).

The question behind it: can a new kind of enrichment be added without knowing
the whole code base, and later be supplied from outside — a local Python
module, a REST API, an MCP tool server, a remote agent?

## Evidence: what one capability touches today

Counts are occurrences of the four capability ids as string literals
(`grep -w '"geocode"'` and so on), release 0.1.0, tests and evaluation code left
out.

| Concern | Where it lives | How it knows the capability |
| --- | --- | --- |
| Typed contract: inputs, parameters with allowed values, output | `capabilities/definitions.py` (`CapabilitySpec`) | one function per capability, registered in `get_builtin_capabilities` |
| Handler, source, configuration id, versions, whether the lookup is audited, its resolution service and policy, the service for a user-approved entity, reuse of confirmed values | `orchestration/live_executor.py` — 28 literals | hand-written dictionaries keyed by id, switched by `audit_mountains` / `audit_capitals` |
| Which lookups are audited in a web run; the policy parameter | `plan_run_worker.py` | `"geocode" in capabilities`, `"resolve_entity" in capabilities`; the request field is named `mountain_resolution_policy` |
| Entity type, which input is the name, approved parameters | `resolution/job.py` — 6 literals | `if capability_id == "geocode"` |
| Search under another name | `resolution/other_name.py` (`_CONTRACTS`, two branches), `resolution/resume.py` (two branches) | a `Literal["geocode", "resolve_entity"]` |
| Re-verification of a chosen entity | `providers/open_meteo_approved.py`, `providers/wikidata_approved.py` | one class per source |
| What the model is told about the source when asked for other names | `spellings.py` (`_SOURCES`) | dictionary keyed by id |
| Checks of a plan against the instruction | `validation/intent_policy.py`, `planning/instructions.py` | the capital role of `geocode` |
| How a step is worded for the user | `frontend/src/planModel.ts` (a `switch` over the four ids), `locales/*.json` (`plan.do.*`, `plan.port.*`) | the id; an unknown capability is shown under its own name |
| "What this tool can do" | `frontend/src/components/CanDo.tsx` | three fixed sentences |

What is already general:

- The planner is given the registry (`describe_all()`), not a fixed list; a plan
  is validated against the registered contracts (`validation/plan_validator.py`).
- Execution is generic over handlers (`execution/job_executor.py`,
  `execution/dag.py`); budgets, retries, memoization and the two caches are
  bound per capability and source, not per name.
- The audit model (`resolution/models.py`: candidates, verification, decision,
  evidence, record links) does not mention cities or mountains.
- Review and Results read the audit; the interface has a fallback for a
  capability it has no words for.

## Inference

- A capability that only **computes** from typed inputs (like
  `geodesic_distance`) needs three places today: its contract, its binding in
  `live_executor.py`, and its wording in the interface.
- A capability that **reads a value from a known record** (like
  `lookup_property`) needs the same three plus a source adapter.
- A capability that **resolves a name** needs most rows of the table: about
  eight backend modules and three interface files, because verification, review,
  search under another name and re-verification are written per capability.
- The parameters are closed lists: `resolve_entity` accepts only
  `entity_type="mountain"`, `lookup_property` only `property="elevation"`.
  Widening either is a contract change plus source logic, not a new capability.
- Every capability added changes what the planner is shown. Planning was
  measured with four capabilities (10 of 10 on two tasks,
  `docs/26-live-planning-reliability.md`); a fifth invalidates that measurement
  until it is repeated.

## What a capability has to answer

The product promises verified values with an audit. A capability from outside
must uphold that, so its declaration has to answer more than "inputs and
outputs":

1. **Contract** — typed inputs, parameters, output and unit. Exists
   (`CapabilitySpec`).
2. **Binding** — handler, source id, request budget, configuration and version
   ids. Exists, spread over `live_executor.py`.
3. **Evidence** — for every value: which source record it came from and what
   was read. Exists per handler; not a stated requirement of the interface.
4. **Kind of trust** — *computed* (deterministic, nothing to verify), *read
   from an identified record*, or *resolved from a name* (candidates, a
   verification rule, a policy for accepting one). Today implicit in which
   dictionary an id appears in.
5. **Review** — for a resolved name: which input is the name, which is its
   context, whether a user may choose a candidate, whether it may be searched
   under another name, how a chosen entity is verified again.
6. **Reuse** — whether a confirmed value may be taken over by a later run, and
   for how long.
7. **Wording** — how the step, its inputs and its failures are named in each
   language, with the capability's own description as the fallback.

## Recommendation

One declaration per capability — call it a `CapabilityBinding` — that answers
points 2–7 next to the existing `CapabilitySpec`, and one registry of bindings
that the executor assembly, the run worker, the resolution job, the review
services and the request for other names read from.

**Step 1 — move, do not change.** Introduce the binding and express the four
built-in capabilities in it. `live_executor.py` builds the executor from the
registry; the name-based branches in `resolution/job.py`, `other_name.py`,
`resume.py`, `spellings.py` and `plan_run_worker.py` read the binding instead.
No behaviour changes; the existing tests are the proof.

**Step 2 — wording and "what it can do" from the server.** A route lists the
registered capabilities with their descriptions; the interface keeps its own
translated wording where it has one and falls back to the description. The
"can do" list is generated.

**Step 3 — prove it with one new capability**, added only through a binding,
and repeat the planning measurement with it. The choice of capability decides
how much of the contract is exercised: a computed one tests little, one that
resolves a name against a new source tests everything.

**Step 4 — bindings whose handler is outside the process.** In this order:
a declarative REST binding (request template, where the value and its record id
are in the answer), an MCP tool binding, a remote agent. Each has to return
evidence in the audit's terms; a value without a record id is not "verified"
and the interface must say so. A remote agent that decides its own steps does
not fit a step of a validated plan and needs its own design.

### Limits and risks

- **Trust.** An external handler can return anything. Points 3 and 4 are what
  keep the audit honest; without them a plug-in system would quietly turn
  "verified" into "returned by something".
- **Code execution.** A local Python module is arbitrary code. It must be an
  opt-in of a local installation and never loadable on a hosted instance.
- **Planner reliability** has to be measured again with every capability set
  that is offered.
- **Two days** fit Step 1 and a small Step 3. Step 2 is optional polish; Step 4
  is a project of its own.

## Decisions for the owner

1. Is Step 1 plus a small Step 3 the scope for the two days?
2. Which capability proves it? Options, by how much they exercise:
   - a second property of an entity already resolved (`lookup_property` beyond
     `elevation`) — small, same source;
   - a value of a city that the city lookup already receives, such as its
     population — no new source, but it needs an output contract for it
     ([#22](https://github.com/ayri77/ai-data-enrichment-studio/issues/22));
   - a new entity type or source, such as an organization
     ([#162](https://github.com/ayri77/ai-data-enrichment-studio/issues/162),
     the third scenario of epic
     [#6](https://github.com/ayri77/ai-data-enrichment-studio/issues/6)) —
     exercises everything and does not fit two days.
