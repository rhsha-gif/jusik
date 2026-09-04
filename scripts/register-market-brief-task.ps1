# Registers (or replaces) the Windows scheduled task that runs the daily market
# brief on weekdays at 16:10 (KRX close 15:30 plus pykrx publication lag).
# Run this yourself from the repository root; the plan does not register it.
#
#   powershell -ExecutionPolicy Bypass -File scripts\register-market-brief-task.ps1
#
# The task runs scripts\run-market-brief.cmd, which cds to the repo and uses
# the project venv. Set NAVER_CLIENT_ID / NAVER_CLIENT_SECRET /
# QUANTPILOT_SLACK_WEBHOOK_URL as *user* environment variables first so the
# scheduled process inherits them.
param(
    [string]$TaskName = "QuantPilot Market Brief",
    [string]$StartTime = "16:10"
)
$repoRoot = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $repoRoot "scripts\run-market-brief.cmd"
if (-not (Test-Path -LiteralPath $wrapper)) { throw "wrapper not found: $wrapper" }
schtasks /Create /TN "$TaskName" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST $StartTime /TR "`"$wrapper`"" /F
if ($LASTEXITCODE -ne 0) { throw "schtasks failed with exit $LASTEXITCODE" }
Write-Host "registered '$TaskName' -> $wrapper at $StartTime on weekdays"
