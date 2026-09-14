# Run the paper trader and independent AI/report worker without a visible console.
[CmdletBinding()]
param(
    [ValidateSet('Start','Stop','Status','Pause','Resume','Flatten','Report','Dashboard')][string]$Action='Status',
    [string]$RuntimeDirectory=(Join-Path $env:USERPROFILE '.quantpilot\intraday'),
    [string]$Python=(Join-Path $env:USERPROFILE '.quantpilot\runtime-venv\Scripts\python.exe'),
    [int]$Port=8770
)
$ErrorActionPreference='Stop'
$projectDirectory=Split-Path -Parent $PSScriptRoot
$pythonPath=(Get-Command $Python -ErrorAction Stop).Source
$arguments=@('-m','quantpilot.paper','--runtime-dir',('"'+$RuntimeDirectory+'"'),'--json')

function Start-HiddenRole {
    param([string]$Role, [string[]]$RoleArguments, [string]$Module='quantpilot.paper')
    # Hidden windows lose their console output, so every role keeps a dated log next to
    # the ledger. Start failures and uncaught tracebacks land there instead of vanishing.
    $logDirectory=Join-Path $RuntimeDirectory 'logs'
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    # Second-resolution stamp: a restart on the same day must not truncate the crash log.
    $stamp=(Get-Date).ToString('yyyyMMdd-HHmmss')
    $roleBase=if($Module -eq 'quantpilot.paper') { $arguments } else { @('-m',$Module,'--runtime-dir',('"'+$RuntimeDirectory+'"')) }
    Start-Process -FilePath $pythonPath -ArgumentList ($roleBase+$RoleArguments) -WorkingDirectory $projectDirectory -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDirectory ($Role+'-'+$stamp+'.out.log')) `
        -RedirectStandardError (Join-Path $logDirectory ($Role+'-'+$stamp+'.err.log'))
}

if($Action -eq 'Start') {
    # Configuration and process environment must explicitly enable paper submission.
    # No flags, credentials, or account settings are changed by this launcher.
    Push-Location -LiteralPath $projectDirectory
    try {
        $state=& $pythonPath -m quantpilot.paper --runtime-dir $RuntimeDirectory --json status | ConvertFrom-Json
        if($LASTEXITCODE -ne 0) { throw 'Paper profile status unavailable.' }
    } finally { Pop-Location }
    if($state.supervisor_enabled) {
        Start-HiddenRole -Role 'supervisor' -Module 'quantpilot.paper.supervisor' -RoleArguments @('start')
    } else {
        Start-HiddenRole -Role 'trader' -RoleArguments @('start')
        Start-HiddenRole -Role 'worker' -RoleArguments @('worker')
        Start-HiddenRole -Role 'reporter' -RoleArguments @('reporter')
    }
    Write-Output ('Logs: '+(Join-Path $RuntimeDirectory 'logs'))
} elseif($Action -eq 'Stop') {
    Push-Location -LiteralPath $projectDirectory
    try { & $pythonPath -m quantpilot.paper.supervisor --runtime-dir $RuntimeDirectory stop }
    finally { Pop-Location }
} elseif($Action -eq 'Dashboard') {
    # Read-only viewer on loopback; it opens the ledger with mode=ro and takes no trader lock.
    Start-HiddenRole -Role 'dashboard' -RoleArguments @('dashboard','--port',$Port)
    Write-Output ('Dashboard: http://127.0.0.1:'+$Port+'/')
} else {
    Push-Location -LiteralPath $projectDirectory
    try { & $pythonPath -m quantpilot.paper --runtime-dir $RuntimeDirectory --json $Action.ToLowerInvariant() }
    finally { Pop-Location }
}
