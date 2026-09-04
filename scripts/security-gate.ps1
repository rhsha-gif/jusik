# Ship-gate evidence collector: gitleaks + semgrep + tach + the diff, written to
# .security-gate\<timestamp>\ for the qp-security-gate agent to judge.
#
#   powershell -ExecutionPolicy Bypass -File scripts\security-gate.ps1 -Staged
#
# Fail-closed: if any tool is missing or cannot run, exit 1 and the gate is
# not satisfied. Findings do NOT change the exit code — counting them is the
# agent's job (summary.json carries the counts). Nothing here reads .env.
param(
    [switch]$Staged,
    [string]$OutDir
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'

$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $OutDir) { $OutDir = Join-Path $repo (".security-gate\" + $stamp) }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$toolVenv = Join-Path $env:USERPROFILE '.local\share\aorch-tools\.venv\Scripts'
$tools = @{
    gitleaks = (Join-Path $env:USERPROFILE 'scoop\shims\gitleaks.exe')
    semgrep  = (Join-Path $toolVenv 'semgrep.exe')
    tach     = (Join-Path $toolVenv 'tach.exe')
}
$summary = [ordered]@{
    generated_at = (Get-Date -Format 's')
    mode         = $(if ($Staged) { 'staged' } else { 'worktree' })
    out_dir      = $OutDir
    tools        = [ordered]@{}
    changed_files = @()
    status       = 'incomplete'
}
function Write-Summary {
    $summary | ConvertTo-Json -Depth 6 | Out-File -LiteralPath (Join-Path $OutDir 'summary.json') -Encoding utf8
}
foreach ($name in $tools.Keys) {
    if (-not (Test-Path -LiteralPath $tools[$name])) {
        $summary.tools[$name] = [ordered]@{ status = 'missing'; path = $tools[$name] }
        $summary.status = 'tool-missing'
        Write-Summary
        Write-Error "security gate cannot run: $name not found at $($tools[$name])"
        exit 1
    }
}

# --- changed files and diff -------------------------------------------------
if ($Staged) {
    $files = @(& git diff --cached --name-only --diff-filter=ACMR)
    & git diff --cached | Out-File -LiteralPath (Join-Path $OutDir 'diff.patch') -Encoding utf8
} else {
    $files = @(& git diff --name-only HEAD --diff-filter=ACMR) + @(& git ls-files --others --exclude-standard)
    & git diff HEAD | Out-File -LiteralPath (Join-Path $OutDir 'diff.patch') -Encoding utf8
}
$files = @($files | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Sort-Object -Unique)
$summary.changed_files = $files

# Native stderr lines become ErrorRecords under Stop; the tools legitimately
# write progress there, so run them under Continue and read $LASTEXITCODE.
$previousPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    # --- gitleaks ------------------------------------------------------------
    $gitleaksReport = Join-Path $OutDir 'gitleaks.json'
    if ($Staged) {
        & $tools.gitleaks git --pre-commit --staged --no-banner --redact --exit-code 0 --report-format json --report-path $gitleaksReport . 2>&1 | Out-Null
    } else {
        & $tools.gitleaks dir . --no-banner --redact --exit-code 0 --report-format json --report-path $gitleaksReport 2>&1 | Out-Null
    }
    $gitleaksExit = $LASTEXITCODE
    $gitleaksCount = 0
    if (Test-Path -LiteralPath $gitleaksReport) {
        $raw = Get-Content -LiteralPath $gitleaksReport -Raw -Encoding UTF8
        # PS 5.1 turns '[]' into $null and @($null).Count is 1, so test the text first.
        if ($raw.Trim() -and $raw.Trim() -ne '[]') { $gitleaksCount = @(ConvertFrom-Json $raw).Count }
    }
    $summary.tools.gitleaks = [ordered]@{ status = $(if ($gitleaksExit -eq 0) { 'ran' } else { 'failed' }); exit = $gitleaksExit; findings = $gitleaksCount; report = $gitleaksReport }

    # --- semgrep (python + ts/js sources only) --------------------------------
    $semgrepReport = Join-Path $OutDir 'semgrep.json'
    $codeFiles = @($files | Where-Object { $_ -match '\.(py|ts|tsx|js|mjs)$' })
    if ($codeFiles.Count -gt 0) {
        & $tools.semgrep scan --config p/python --config p/secrets --config p/security-audit --json --output $semgrepReport --quiet --metrics=off @codeFiles 2>&1 | Out-Null
        $semgrepExit = $LASTEXITCODE
        $semgrepCount = 0
        if (Test-Path -LiteralPath $semgrepReport) {
            $parsed = Get-Content -LiteralPath $semgrepReport -Raw -Encoding UTF8 | ConvertFrom-Json
            $semgrepCount = @($parsed.results).Count
        }
        $summary.tools.semgrep = [ordered]@{ status = $(if ($semgrepExit -eq 0) { 'ran' } else { 'failed' }); exit = $semgrepExit; findings = $semgrepCount; report = $semgrepReport; files = $codeFiles.Count }
    } else {
        '{"results": [], "errors": [], "note": "no python/ts files changed"}' | Out-File -LiteralPath $semgrepReport -Encoding utf8
        $semgrepExit = 0
        $summary.tools.semgrep = [ordered]@{ status = 'skipped-no-code-files'; exit = 0; findings = 0; report = $semgrepReport; files = 0 }
    }

    # --- tach ------------------------------------------------------------------
    $tachReport = Join-Path $OutDir 'tach.txt'
    & $tools.tach check 2>&1 | Out-File -LiteralPath $tachReport -Encoding utf8
    $tachExit = $LASTEXITCODE
    $summary.tools.tach = [ordered]@{ status = 'ran'; exit = $tachExit; violations = $(if ($tachExit -eq 0) { 0 } else { 1 }); report = $tachReport }
} finally {
    $ErrorActionPreference = $previousPref
}

if ($gitleaksExit -ne 0 -or $semgrepExit -ne 0) {
    $summary.status = 'tool-failed'
    Write-Summary
    Write-Error "security gate tool failed (gitleaks=$gitleaksExit semgrep=$semgrepExit); see $OutDir"
    exit 1
}
$summary.status = 'complete'
Write-Summary
Write-Host "security gate evidence: $OutDir (gitleaks findings=$gitleaksCount, semgrep findings=$($summary.tools.semgrep.findings), tach exit=$tachExit, files=$($files.Count))"
exit 0
