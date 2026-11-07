# Mountain Elevation — Review, Approve & Resume

This demo illustrates audited entity resolution and human-in-the-loop data enrichment using Wikidata.

## Objective

Enrich an Excel workbook containing mountain names and countries with elevation data while preserving source evidence and a traceable resolution history.

## Workflow

1. **Initial execution:** The system searches Wikidata for "Mount Everest" in Nepal.
2. **Review required:** Q513 is verified, but the overall candidate verification is incomplete. The job enters `awaiting_review`.
3. **Human approval:** The user explicitly approves Q513 using an approval manifest bound to the original workbook and job.
4. **Resume:** The system re-verifies Q513's entity type and country without repeating text-based entity discovery.
5. **Enrichment:** The downstream property lookup retrieves the elevation and writes the result to Excel.

## Results

| Field | Result |
|---|---|
| Entity | Mount Everest |
| Wikidata ID | Q513 |
| Country | Nepal |
| Elevation | 8848.86 m |
| Final job status | `completed` |
| Resolution status | `resolved` |
| Decision basis | `user_override` |
| Successful rows | 1 of 1 |

## Files

| File | Purpose |
|---|---|
| `mountains_smoke.xlsx` | Source workbook |
| `mountains_plan.json` | Enrichment execution plan |
| `columns.json` | Excel output column mapping |
| `mountains_enriched_audited.resolution.json` | Original resolution audit |
| `mountains_approvals.json` | User-approved candidate |
| `mountains_resumed.xlsx` | Final enriched workbook |
| `mountains_resumed.resolution.json` | Final resolution audit |

## Reproduce the Resume Step

Run from the repository root:

```powershell
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

The `temp/` directory must exist before running this command.

The command uses live Wikidata requests. The returned elevation may change if the source data changes.

**Important:** The approval manifest is bound to the exact source workbook (SHA-256) and original resolution job. Modifying or regenerating these files requires preparing a matching approval manifest.

## Key Design Principles

- Human approval does not bypass entity verification.
- Approvals are scoped to individual records and DAG steps.
- Approved resolutions do not leak into the cache for other records.
- Original and resumed jobs are linked through `previous_job_id`.
- Resolution decisions retain supporting evidence for auditing.