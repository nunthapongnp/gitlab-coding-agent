# Host launcher for the coding agent (Windows PowerShell). This wraps THE SAME
# application invocation as scripts/launch.sh: `python main.py <args>` inside
# the coordinator container.
#
# Per launch (P0-SPEC-003 section 4.2):
#   - a unique OPERATION_ID is generated;
#   - a distinct Compose project (and therefore distinct per-operation
#     workspace volume) is started;
#   - CLI arguments are forwarded unchanged to python main.py;
#   - artifacts are kept OUTSIDE the product checkout.
#
# Retention contract: nothing is ever cleaned up automatically. Neither on
# success nor on failure does this script run `docker compose down --volumes`
# or delete/reset volumes, workspaces, or logs. Failed-run evidence is retained
# for operator inspection. Successful-run cleanup is an explicit operator
# action (commands are printed below; see README).

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

# Unique operation identifier for this launch.
if (-not $env:OPERATION_ID) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $env:OPERATION_ID = "op-$stamp-$(([guid]::NewGuid().ToString('N')).Substring(0, 8))"
}

# Distinct Compose project per launch -> distinct per-operation volumes.
$env:COMPOSE_PROJECT_NAME = "gitlab-agent-$($env:OPERATION_ID)"

# Artifacts live outside the product checkout by default.
if (-not $env:AGENT_ARTIFACT_DIR) {
    $env:AGENT_ARTIFACT_DIR = Join-Path $HOME ".gitlab-coding-agent/artifacts"
}
New-Item -ItemType Directory -Force $env:AGENT_ARTIFACT_DIR | Out-Null

# The shared control volume (Issue/branch lock metadata) is shared across all
# launches on this host; create it once if missing. It is never deleted here.
docker volume create gitlab-agent-control | Out-Null

Write-Host "Operation        : $($env:OPERATION_ID)"
Write-Host "Compose project  : $($env:COMPOSE_PROJECT_NAME)"
Write-Host "Artifacts        : $($env:AGENT_ARTIFACT_DIR) (retained on failure; cleanup is manual)"

Push-Location $repoRoot
try {
    # Forward ALL CLI arguments to the same `python main.py` invocation.
    docker compose run --rm coordinator @args
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

$project = $env:COMPOSE_PROJECT_NAME
if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "Launch FAILED (exit $exitCode). NO cleanup was performed; evidence is retained for inspection:" -ForegroundColor Red
    Write-Host "  compose project : $project (docker compose -p $project ps -a)"
    Write-Host "  workspace volume: ${project}_workspace"
    Write-Host "  artifacts/logs  : $($env:AGENT_ARTIFACT_DIR)/$($env:OPERATION_ID)"
    Write-Host "  control volume  : gitlab-agent-control"
    Write-Host "Manual cleanup (explicit operator action; never automatic):"
    Write-Host "  docker compose -p $project down    # containers only; volumes are kept"
    Write-Host "  docker volume rm ${project}_workspace  # ONLY if you want the workspace gone"
} else {
    Write-Host ""
    Write-Host "Launch finished (exit 0). Storage is retained; cleanup is an explicit operator action:"
    Write-Host "  docker compose -p $project down; docker volume rm ${project}_workspace"
}

exit $exitCode
