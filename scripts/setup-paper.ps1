[CmdletBinding()]
param(
    [string]$Python='python',
    [string]$EnvironmentDirectory=(Join-Path $env:USERPROFILE '.quantpilot\runtime-venv')
)
$ErrorActionPreference='Stop'
$runtimePython=Join-Path $EnvironmentDirectory 'Scripts\python.exe'
if(-not (Test-Path -LiteralPath $runtimePython)) {
    & $Python -m venv $EnvironmentDirectory
    if($LASTEXITCODE -ne 0) { throw 'paper_environment_creation_failed' }
}
& $runtimePython -m pip install 'fastapi==0.133.1' 'starlette==1.0.1' pydantic pyyaml uvicorn httpx pytest 'exchange-calendars==4.13.2'
if($LASTEXITCODE -ne 0) { throw 'paper_dependency_install_failed' }
Write-Output 'Paper environment is installed. Trading remains disabled.'
