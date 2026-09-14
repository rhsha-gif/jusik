# Registers (or replaces) the Windows scheduled task that runs the weekly macro
# outlook on Sunday evening, before the Monday open. Run this yourself from
# the repository root; the plan does not register it.
#
#   powershell -ExecutionPolicy Bypass -File scripts\register-macro-outlook-task.ps1
#
# The task runs scripts\run-macro-outlook.cmd, which cds to the repo, uses the
# project venv and loads credentials through scripts\run-with-env.ps1 from
# %USERPROFILE%\.quantpilot-research.sources (add the env file that carries
# ECOS_API_KEY / FRED_API_KEY there with an `only=` filter).
param(
    [string]$TaskName = "QuantPilot Macro Outlook",
    [string]$StartTime = "20:00",
    [string]$Day = "SUN"
)
$repoRoot = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $repoRoot "scripts\run-macro-outlook.cmd"
if (-not (Test-Path -LiteralPath $wrapper)) { throw "wrapper not found: $wrapper" }
schtasks /Create /TN "$TaskName" /SC WEEKLY /D $Day /ST $StartTime /TR "`"$wrapper`"" /F
if ($LASTEXITCODE -ne 0) { throw "schtasks failed with exit $LASTEXITCODE" }
Write-Host "registered '$TaskName' -> $wrapper at $StartTime on $Day"
