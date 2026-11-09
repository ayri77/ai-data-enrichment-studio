# Product Roadmap: Data IDE, Visual Workflow Studio, Decision Workbench, Autonomous Agent

**Agreed:** 2026-11-09 · **Owner:** M5 [Epic #6](https://github.com/ayri77/ai-data-enrichment-studio/issues/6) · **Status:** design exploration and local E2E integration are in progress; not a claim of completed UI/agent work.

## 1. Product direction

One **AI Data Enrichment Studio**, with shared shell, navigation, state semantics, typography, accessibility, and API contracts, but three **different specialized working modes**:

| Mode | Primary interaction | Intended screens |
| --- | --- | --- |
| **Data IDE** | High-information-density spreadsheet grid, column inspector, keyboard and guided range selection | Dataset / source and enriched result / Export |
| **Visual Workflow Studio** | Validated typed DAG, plan/tool inspectors, observed execution traces and contextual events | Plan / Execution / optional Agent trace |
| **Decision Workbench** | Grouped uncertainty queue, source-qualified candidate comparison, Evidence, scoped approval and audited resume | Review / Clarification |

This intentionally **does not** mean three color palettes of an identical SaaS template. Each mode may use a distinct central layout and controls, under a coherent product design system. Codex Sites explorations remain independent visual prototypes; do not copy their simulated results into the real application. Keep the logo provisional.

### Language contract

English (default), **Ukrainian**, and **German** only. No Russian UI localization, locale entry, or translation resources. Use a frontend i18n framework, stable backend error codes, persisted language preference, no loss of the current dataset when language changes. Never translate sheet names, input cells, IDs, Evidence, or LLM-generated user data without explicit user action.

## 2. Non-negotiable first deliverable: actual two-file E2E

The first product gate is a *real* local browser workflow through the existing Python code, not the completion of all planned screens:

1. Upload `samples/capitals.xlsx` or `samples/mountains.xlsx`; inspect/select the actual worksheet and range; preserve the source bytes.
2. Describe the enrichment goal in natural language; generate or reuse an EnrichmentPlan; run typed validation and **explicit human plan approval**.
3. Start the bounded plan-first execution against approved registered tools/providers; retain real structured outcome/error/audit data. Separate simulation results entirely.
4. Present per-record status (completed / partial / failed / needs-review) and output values **only where verified under the selected policy**.
5. Export a distinct enriched `.xlsx` with original cells preserved; expose a reproducible report. UI may use basic forms and lists before DAG/Review design is ready.

**Previously observed real baseline (not a guarantee for later network runs):** Mountains 9/10 success under opt-in `verified_only`, with Annapurna unresolved; Capitals 6/10 successful, four partial rows caused by two contextual city ambiguities (Kyiv, Berlin). Istanbul must be treated as the **literal city**, never silently corrected to Ankara or forced into a country-capital role. Candidate ordering/popularity alone is not sufficient evidence. Never show simulated `10/10 approved` or fabricated distances as real results.

A planned UI control is permitted if it is *visibly* disabled with a plain description. A separate **Demo / Simulation** pathway is permitted for visual exploration, but must be labeled throughout and **must not** generate real approval artifacts, source Evidence or exported workbooks. Do not introduce silent fallback from real execution to a simulated success.

### E2E gates

- **E2E-0 / real core, first:** Python API + minimally connected UI: source → plan → approve → bounded run → download XLSX + truthful issue summary for **both** official files. Some values may remain unresolved; this is correct.
- **E2E-1 / improved comprehension:** replace visual placeholders with actual DAG Plan Studio and real replayable execution events. No duplicate resolver/runner in React.
- **E2E-2 / human disambiguation:** audited candidates → scoped explicit approvals → fresh re-verification → resume, followed by enriched XLSX and source-linked audit. Kyiv/Berlin should group to two distinct queries for four affected rows. Annapurna cannot be approved if the desired summit was not positively verified in the candidate set; offer clarification followed by new verification or keep unresolved.
- **Post-demo / scale:** policy settings, bulk impact preview, repeated-query dedup, tested resource envelope for synthetic 1k/5k/10k rows. Do not assume existing M5-1 upload/worksheet limits support 10k; increase them only with measured safe tests.

## 3. Architecture: preserve one trusted backend

Existing Python components remain authoritative: `DatasetProfile`, `EnrichmentPlan`, `PlanValidator`, approved capabilities/providers, `JobExecutor`, Resolution Reports, scoped approval manifests, safe Excel writer, and bounded LangGraph M4 workflow.

The thin FastAPI layer should expose stable, source-bound application actions and opaque IDs:

```text
Upload / select → generate+validate plan → approve plan → start bounded run
    → job statuses + operation/review artifacts → approve candidate(s) and resume
    → result workbook download + auditable report
```

Review and the run API must share the same source/workbook checksum, per-record/step identity and evidence rules. A UI button is never an authorization to bypass provider checks. Review semantics must be meaningful for both `geocode` and `resolve_entity`; the initial mountain-only Resume implementation does not automatically imply working city approvals.

For observability, both run modes should **eventually** emit a bounded typed **execution-event envelope** with job/mode, time/order, provider/tool/step, record/operation identity, state transition, duration, error and Evidence references, with redaction limits. Distinguish:

- **Planned Workflow:** prevalidated static DAG plus observed node transitions.
- **Autonomous Agent:** actual discovered sequence/graph of tool calls **as it happens**. The whole path is not known beforehand; do not fabricate a static graph or expose a model's hidden internal reasoning.

Start execution UI with replay/polling of **recorded** events; add SSE/live event streaming only after its backend correctness is proven. Aggregate row-heavy execution data and inspect a selected row on demand. Animation is a visualization of genuine events, not a source of truth.

## 4. Parallel research track: genuine autonomous agent

The existing completed [M4 #5](https://github.com/ayri77/ai-data-enrichment-studio/issues/5) is a **bounded plan/validate/execute/repair orchestration**, not a free tool-choosing agent. Keep it closed. The optional autonomous mode belongs in separate, gated work:

1. **[Agent R1 #106](https://github.com/ayri77/ai-data-enrichment-studio/issues/106):** Python-only, opt-in dynamic action selection; begins with one official input record; registered allowlisted tools and the same verification/Evidence contracts; strict iteration/time/tool/token/cost caps, safe abstention and offline tests. No raw arbitrary code execution, direct Excel writes, permission bypass or silent unverified facts.
2. **[Agent R2 #107](https://github.com/ayri77/ai-data-enrichment-studio/issues/107):** controlled comparison of planned workflow versus agent on the same data: correctness, coverage, uncertainty, source evidence, latency, provider calls, tokens/cost, reproducibility and actual action traces. Later plug the dynamic trace into the same Visual Workflow Studio event viewer.

Work on Agent R1 can begin **in parallel with UI design** on its own reviewed Python feature branch; do not block or destabilize the M5 E2E path. Prefer a small independent vertical slice and reuse existing providers/models instead of creating an unrelated agent framework. The user may focus on Python/backend while Codex implements React/TypeScript UI.

## 5. Delivery map

| Stage | Issues | Merge/acceptance gate |
| --- | --- | --- |
| M5-1 | [#99](https://github.com/ayri77/ai-data-enrichment-studio/issues/99), [PR #98](https://github.com/ayri77/ai-data-enrichment-studio/pull/98) | Select visual system; finalize working Dataset UI, guided range, EN/UK/DE; merge only after review/CI |
| M5-2 | [#100](https://github.com/ayri77/ai-data-enrichment-studio/issues/100) | Real backend plan/run/artifact API; no mock output |
| **E2E-0** | [#104](https://github.com/ayri77/ai-data-enrichment-studio/issues/104) | **Both official workbooks run via minimal real UI** and produce separate enriched XLSX + truthful status |
| M5-3 | [#101](https://github.com/ayri77/ai-data-enrichment-studio/issues/101) | Real validated DAG visualization and approval |
| M5-4 | [#102](https://github.com/ayri77/ai-data-enrichment-studio/issues/102) | Real bounded event-trace viewer, initially replay |
| M5-5 | [#103](https://github.com/ayri77/ai-data-enrichment-studio/issues/103) + [#68](https://github.com/ayri77/ai-data-enrichment-studio/issues/68) | Verified Kyiv/Berlin city Review and Resume; unresolved Annapurna handled honestly |
| **E2E-2** | [#104](https://github.com/ayri77/ai-data-enrichment-studio/issues/104) | Complete reviewed two-file demo, export/audit, tests and explicit remaining limits |
| Later | [#105](https://github.com/ayri77/ai-data-enrichment-studio/issues/105) | Controlled review-policy and synthetic scale testing |
| Python R&D parallel | [#106](https://github.com/ayri77/ai-data-enrichment-studio/issues/106) → [#107](https://github.com/ayri77/ai-data-enrichment-studio/issues/107) | Dynamic agent + fair workflow comparison |

**Current snapshot:** M4 core finished; M5-1 functional implementation lives in **unmerged PR #98**; three product designs are being explored separately. [Academic Acceptance #95](https://github.com/ayri77/ai-data-enrichment-studio/issues/95) was observed **closed** on 2026-11-09 even though its checklist retains unchecked independent numeric and submission items. These factual evaluation tasks are explicitly included in [#104](https://github.com/ayri77/ai-data-enrichment-studio/issues/104), without silently asserting their completion.

## 6. Working agreements / scope guards

- One reviewed, CI-green PR per bounded vertical slice. Do not push directly to protected `main` or alter uncommitted work in other branches/worktrees.
- Codex Sites/Figma are *visual exploration* tools; source of truth for runtime behavior stays in verified Python service contracts and the accepted React application.
- Real and simulated data/states are explicitly separated and visibly labeled; no UI deception by checkboxes or 'Approve all' for unverified candidates.
- Correct unresolved/partial result is a valid outcome; do not chase 10/10 by unsafe heuristics.
- Final academic correctness requires independent numerical comparison and source immutability checks; successful execution alone is not proof of accuracy.
- Defer hosted SaaS, authentication/tenancy, third domain, persistent distributed queues, sweeping refactors and final branded logo until the real demo is stable.
