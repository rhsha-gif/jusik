# Run the paper trader and independent AI/report worker without a visible console.
[CmdletBinding()]
param(
    [ValidateSet('Start','Status','Pause','Resume','Flatten','Report')][string]$Action='Status',
    [string]$RuntimeDirectory=(Join-Path $env:USERPROFILE '.quantpilot\intraday'),
    [string]$Python=(Join-Path $env:USERPROFILE '.quantpilot\runtime-venv\Scripts\python.exe')
)
$ErrorActionPreference='Stop'
$projectDirectory=Split-Path -Parent $PSScriptRoot
$pythonPath=(Get-Command $Python -ErrorAction Stop).Source
$arguments=@('-m','quantpilot.paper','--runtime-dir',('"'+$RuntimeDirectory+'"'),'--json')
if($Action -eq 'Start') {
    # Configuration and process environment must explicitly enable paper submission.
    # No flags, credentials, or account settings are changed by this launcher.
    Start-Process -FilePath $pythonPath -ArgumentList ($arguments+@('start')) -WorkingDirectory $projectDirectory -WindowStyle Hidden
    Start-Process -FilePath $pythonPath -ArgumentList ($arguments+@('worker')) -WorkingDirectory $projectDirectory -WindowStyle Hidden
    Start-Process -FilePath $pythonPath -ArgumentList ($arguments+@('reporter')) -WorkingDirectory $projectDirectory -WindowStyle Hidden
} else {
    Push-Location -LiteralPath $projectDirectory
    try { & $pythonPath -m quantpilot.paper --runtime-dir $RuntimeDirectory --json $Action.ToLowerInvariant() }
    finally { Pop-Location }
}
