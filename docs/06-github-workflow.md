# GitHub Development Workflow

**Status:** Suggested baseline (activate after first CI pass).

## Issue taxonomy

Use a few type labels:

- `type:bug`: Broken existing behavior.
- `type:feature`: New user-facing capability.
- `type:enhancement`: Improvement to existing behavior.
- `type:chore`: Maintenance, CI, dependency and refactoring work.
- `type:docs`: Documentation-only updates.

Optional priority labels: `priority:p0` (blocking), `priority:p1` (important), `priority:p2` (normal). Do not require priority on every issue.

## Milestones

- `M1 — Planner & Validator`
- `M2 — Execution Engine`
- `M3 — Excel & Evaluation`
- `M4 — LangGraph Orchestration`
- `M5 — Portfolio & Deployment`

Milestones represent deliverable stages, not releases or calendar deadlines.

## Hierarchy and effort

Use native GitHub sub-issues for larger epics, with small issues as children. Keep most issues achievable in a single focused PR.

Track effort as S/M/L in the issue form initially. If a GitHub Project is introduced, use its Number field `Estimate` instead of duplicating labels.

## Branch and PR policy

- Branch from `main`: `feat/<topic>`, `fix/<topic>`, `chore/<topic>`.
- Prefer one coherent change per PR.
- Link issues with `Closes #123` where appropriate.
- CI must pass before merge.
- Prefer squash merges and linear history.
- Do not require your own approval on a solo-maintainer repository.

## Main branch protection

After CI is green, create a GitHub ruleset targeting `main`:

1. Require a pull request before merging.
2. Require the CI check `quality` to pass.
3. Block force pushes and branch deletion.
4. Allow squash merges.

Use the repository's supported plan and visibility settings. Avoid enforcing a status check before it has existed on a completed workflow run.
