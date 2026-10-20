# One-time, idempotent repository management setup.
# Requires GitHub CLI (gh), authenticated as a repository administrator.
# Run from PowerShell: .\scripts\setup-github.ps1
param(
    [string]$Repository = "ayri77/ai-data-enrichment-studio",
    [switch]$SkipRuleset
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI not found. Install it with 'winget install --id GitHub.cli', then run 'gh auth login'."
}
& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run 'gh auth login' first."
}

function Get-GhApi {
    param([string]$Path)
    $response = & gh api $Path
    if ($LASTEXITCODE -ne 0) { throw "GitHub API GET failed: $Path" }
    return ($response | ConvertFrom-Json)
}

function Post-GhApi {
    param([string]$Path, [object]$Body)
    $json = ConvertTo-Json -InputObject $Body -Depth 20 -Compress
    $response = $json | & gh api $Path --method POST --input -
    if ($LASTEXITCODE -ne 0) { throw "GitHub API POST failed: $Path" }
    return ($response | ConvertFrom-Json)
}

Write-Host "Configuring $Repository ..."

# Labels: type, priority and effort. Use --force to update existing definitions.
$labels = @(
    @{ Name="type:epic";        Color="8250df"; Description="Parent issue for a project milestone" },
    @{ Name="type:bug";         Color="d73a4a"; Description="Confirmed defect in existing behavior" },
    @{ Name="type:feature";     Color="1f883d"; Description="New user-facing or core functionality" },
    @{ Name="type:enhancement"; Color="0969da"; Description="Improvement to existing functionality" },
    @{ Name="type:chore";       Color="6e7781"; Description="Infrastructure, tooling and maintenance" },
    @{ Name="type:docs";        Color="0075ca"; Description="Documentation work" },
    @{ Name="priority:p0";      Color="b60205"; Description="Critical blocker, address immediately" },
    @{ Name="priority:p1";      Color="d93f0b"; Description="Required for the active milestone" },
    @{ Name="priority:p2";      Color="fbca04"; Description="Planned, not immediately blocking" },
    @{ Name="size:S";           Color="c2e0c6"; Description="A few hours or less" },
    @{ Name="size:M";           Color="bfdadc"; Description="Roughly one to two working days" },
    @{ Name="size:L";           Color="f9d0c4"; Description="Large task; consider splitting" }
)
foreach ($label in $labels) {
    & gh label create $label.Name --repo $Repository --color $label.Color --description $label.Description --force | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Cannot create/update label: $($label.Name)" }
}
Write-Host "Labels configured: $($labels.Count)"

# Milestones have no arbitrary due dates. These are delivery phases, not calendar promises.
$milestoneSpecs = @(
    @{ Title="M1 - Planner & Validator"; Description="Typed contracts, plan generation, deterministic validation and M1 tests" },
    @{ Title="M2 - Execution Engine"; Description="Capabilities, providers, execution DAG, caching and fault handling" },
    @{ Title="M3 - Excel & Evaluation"; Description="Workbook output, independent evaluation and required academic MVP" },
    @{ Title="M4 - LangGraph Orchestration"; Description="Stateful agentic control, bounded recovery and tracing" },
    @{ Title="M5 - Portfolio & Deployment"; Description="Research organization scenario, UI, API, deployment and observability" }
)
$existingMilestones = @(Get-GhApi "repos/$Repository/milestones?state=all&per_page=100")
foreach ($spec in $milestoneSpecs) {
    if (@($existingMilestones | Where-Object { $_.title -eq $spec.Title }).Count -eq 0) {
        $created = Post-GhApi "repos/$Repository/milestones" @{
            title = $spec.Title
            description = $spec.Description
            state = "open"
        }
        $existingMilestones += $created
        Write-Host "Created milestone: $($spec.Title)"
    }
}

# Assign labels and milestones to the issues already created for M1-M5.
$assignments = @(
    @{ Number=2;  Milestone=0; Labels="type:epic,priority:p1" },
    @{ Number=3;  Milestone=1; Labels="type:epic,priority:p2" },
    @{ Number=4;  Milestone=2; Labels="type:epic,priority:p2" },
    @{ Number=5;  Milestone=3; Labels="type:epic,priority:p2" },
    @{ Number=6;  Milestone=4; Labels="type:epic,priority:p2" },
    @{ Number=7;  Milestone=0; Labels="type:feature,priority:p1,size:M" },
    @{ Number=8;  Milestone=0; Labels="type:enhancement,priority:p1,size:S" },
    @{ Number=9;  Milestone=0; Labels="type:feature,priority:p1,size:M" },
    @{ Number=10; Milestone=0; Labels="type:feature,priority:p1,size:L" },
    @{ Number=11; Milestone=0; Labels="type:feature,priority:p1,size:M" },
    @{ Number=12; Milestone=0; Labels="type:feature,priority:p1,size:M" }
)
foreach ($item in $assignments) {
    $title = $milestoneSpecs[$item.Milestone].Title
    & gh issue edit $item.Number --repo $Repository --milestone $title --add-label $item.Labels | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Cannot update issue #$($item.Number)" }
}
Write-Host "Issues labeled and assigned to milestones: $($assignments.Count)"

# Create actual native GitHub parent/sub-issue relationships (not just Markdown links).
$existingChildren = @(Get-GhApi "repos/$Repository/issues/2/sub_issues")
foreach ($number in 7..12) {
    if (@($existingChildren | Where-Object { $_.number -eq $number }).Count -eq 0) {
        $child = Get-GhApi "repos/$Repository/issues/$number"
        $null = Post-GhApi "repos/$Repository/issues/2/sub_issues" @{ sub_issue_id = [int64]$child.id }
        Write-Host "Linked sub-issue #$number to epic #2"
    }
}

if (-not $SkipRuleset) {
    $rulesetName = "Protect main (PR + CI)"
    $existingRulesets = @(Get-GhApi "repos/$Repository/rulesets")
    if (@($existingRulesets | Where-Object { $_.name -eq $rulesetName }).Count -eq 0) {
        # The 'quality' check name was verified against the working CI job.
        $ruleset = @{
            name = $rulesetName
            target = "branch"
            enforcement = "active"
            conditions = @{ ref_name = @{ include = @("refs/heads/main"); exclude = @() } }
            rules = @(
                @{ type = "deletion" },
                @{ type = "non_fast_forward" },
                @{ type = "pull_request"; parameters = @{
                    dismiss_stale_reviews_on_push = $false
                    require_code_owner_review = $false
                    require_last_push_approval = $false
                    required_approving_review_count = 0
                    required_review_thread_resolution = $false
                }},
                @{ type = "required_status_checks"; parameters = @{
                    strict_required_status_checks_policy = $false
                    required_status_checks = @(@{ context = "quality" })
                }}
            )
        }
        $createdRule = Post-GhApi "repos/$Repository/rulesets" $ruleset
        Write-Host "Created active branch ruleset: $($createdRule.name)"
    } else {
        Write-Host "Ruleset already exists; leaving its settings unchanged."
    }
} else {
    Write-Host "Ruleset setup skipped."
}
Write-Host "Setup complete. Review Issues, Milestones, Labels and Rules in GitHub Settings."
