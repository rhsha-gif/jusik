# Loads credentials into THIS process's environment from sources that live
# outside the repository, then runs the given command. The research jobs read
# credentials from the environment only, so this is the bridge for manual runs
# and for the scheduled task (see run-market-brief.cmd). Values are never printed.
#
# Sources (all optional, combined in order; later sources do not overwrite earlier ones):
#   -EnvFile <path>            KEY=VALUE lines (# comments allowed)
#   -Only KEY1,KEY2            take only these keys from -EnvFile
#   -FromClaudeMcp <server>    the `env` block of that MCP server in ~/.claude.json
#                              (e.g. naver-search → NCP_APIGW_API_KEY_ID / NCP_APIGW_API_KEY)
#   -Sources <path>            a file listing sources, one per line:
#                                envfile=<path>|only=KEY1,KEY2
#                                claude-mcp=<server>
#                              default: %USERPROFILE%\.quantpilot-research.sources when it exists
#
#   powershell -ExecutionPolicy Bypass -File scripts\run-with-env.ps1 `
#       --% .\.venv\Scripts\python.exe -m quantpilot.jobs.run_market_brief --no-post
# `--%` (stop-parsing) must precede the command: without it PowerShell reads the job's own
# `--out-dir`/`--dry-run` flags as script parameters (measured: AmbiguousParameter).
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$EnvFile,
    [string]$Only,
    [string]$FromClaudeMcp,
    [string]$Sources,
    [Parameter(ValueFromRemainingArguments)][string[]]$Command
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$script:loaded = @()

function Set-IfAbsent([string]$Name, [string]$Value) {
    if (-not $Name -or -not $Value) { return }
    if (Test-Path -Path ("Env:" + $Name)) { return }
    Set-Item -Path ("Env:" + $Name) -Value $Value
    $script:loaded += $Name
}

function Import-EnvFile([string]$Path, [string[]]$OnlyKeys) {
    if (-not (Test-Path -LiteralPath $Path)) { throw "env file not found: $Path" }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $index = $trimmed.IndexOf('=')
        if ($index -lt 1) { continue }
        $name = $trimmed.Substring(0, $index).Trim()
        $value = $trimmed.Substring($index + 1).Trim().Trim('"')
        if ($OnlyKeys -and ($OnlyKeys -notcontains $name)) { continue }
        Set-IfAbsent $name $value
    }
}

function Import-ClaudeMcpEnv([string]$Server) {
    $configPath = Join-Path $env:USERPROFILE '.claude.json'
    if (-not (Test-Path -LiteralPath $configPath)) { throw "claude config not found: $configPath" }
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $entry = $config.mcpServers.$Server
    if ($null -eq $entry -or $null -eq $entry.env) { throw "MCP server '$Server' has no env block in $configPath" }
    foreach ($prop in $entry.env.PSObject.Properties) { Set-IfAbsent $prop.Name ([string]$prop.Value) }
}

if ($EnvFile) {
    $keys = @()
    if ($Only) { $keys = @($Only.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
    Import-EnvFile $EnvFile $keys
}
if ($FromClaudeMcp) { Import-ClaudeMcpEnv $FromClaudeMcp }
if (-not $Sources) {
    $default = Join-Path $env:USERPROFILE '.quantpilot-research.sources'
    if (Test-Path -LiteralPath $default) { $Sources = $default }
}
if ($Sources) {
    foreach ($line in Get-Content -LiteralPath $Sources -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        if ($trimmed.StartsWith('envfile=')) {
            $parts = $trimmed.Substring(8).Split('|')
            $keys = @()
            foreach ($part in $parts[1..($parts.Count - 1)]) {
                if ($part.StartsWith('only=')) { $keys = @($part.Substring(5).Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
            }
            Import-EnvFile $parts[0].Trim() $keys
        } elseif ($trimmed.StartsWith('claude-mcp=')) {
            Import-ClaudeMcpEnv $trimmed.Substring(11).Trim()
        }
    }
}
Write-Host ("loaded " + $script:loaded.Count + " variable(s): " + ($script:loaded -join ', ') + " (values not shown)")

if ($Command.Count -eq 0) { throw "no command given after --" }
if ($Command[0] -eq '--') { $Command = $Command[1..($Command.Count - 1)] }
$exe = $Command[0]
$cmdArgs = @()
if ($Command.Count -gt 1) { $cmdArgs = $Command[1..($Command.Count - 1)] }
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $exe @cmdArgs
$code = $LASTEXITCODE
$ErrorActionPreference = $previous
exit $code
