# Loads KEY=VALUE lines from a file that lives OUTSIDE the repository into this
# process's environment, then runs the given command. The research jobs read
# credentials from the environment only, so this is the manual-run bridge:
#
#   powershell -ExecutionPolicy Bypass -File scripts\run-with-env.ps1 `
#       -EnvFile "$env:USERPROFILE\.quantpilot-research.env" `
#       -- .\.venv\Scripts\python.exe -m quantpilot.jobs.run_market_brief --no-post
#
# Values are never printed. Blank lines and lines starting with # are ignored.
param(
    [Parameter(Mandatory)][string]$EnvFile,
    [Parameter(ValueFromRemainingArguments)][string[]]$Command
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
if (-not (Test-Path -LiteralPath $EnvFile)) { throw "env file not found: $EnvFile" }
$loaded = 0
foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    $index = $trimmed.IndexOf('=')
    if ($index -lt 1) { continue }
    $name = $trimmed.Substring(0, $index).Trim()
    $value = $trimmed.Substring($index + 1).Trim().Trim('"')
    Set-Item -Path ("Env:" + $name) -Value $value
    $loaded += 1
}
Write-Host "loaded $loaded variable(s) from $EnvFile (values not shown)"
if ($Command.Count -eq 0) { throw "no command given after --" }
if ($Command[0] -eq '--') { $Command = $Command[1..($Command.Count - 1)] }
$exe = $Command[0]
$args = @()
if ($Command.Count -gt 1) { $args = $Command[1..($Command.Count - 1)] }
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $exe @args
$code = $LASTEXITCODE
$ErrorActionPreference = $previous
exit $code
