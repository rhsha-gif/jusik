# Run in a fresh PowerShell process. Never reads .env or prints credentials.
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Prepare', 'Smoke')][string]$Action = 'Check',
    [string]$RuntimeDirectory = (Join-Path $env:USERPROFILE '.quantpilot\paper'),
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$kisNames = @('KIS_PAPER_APP_KEY', 'KIS_PAPER_APP_SECRET', 'KIS_PAPER_ACCOUNT_NUMBER',
              'KIS_PAPER_PRODUCT_CODE', 'KIS_PAPER_ACCESS_TOKEN')
$changedNames = @($kisNames) + @('LIVE_TRADING_ENABLED', 'GUARDED_AUTOPILOT_ENABLED',
    'FULLY_AUTOMATED_OPERATOR_ENABLED', 'MARKET_ORDERS_ENABLED', 'BROKER_MODE',
    'DATA_MODE', 'KIS_PAPER_SESSION_ENABLED', 'KIS_PAPER_ORDER_SUBMISSION_ENABLED',
    'QUANTPILOT_RUNTIME_ROLE')
$savedValues = @{}
foreach ($name in $changedNames) { $savedValues[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
Push-Location -LiteralPath $projectDirectory
try {
    foreach ($name in $kisNames) {
        $value = if ($Action -eq 'Smoke') { $null } else { [Environment]::GetEnvironmentVariable($name, 'User') }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
    foreach ($name in @('LIVE_TRADING_ENABLED', 'GUARDED_AUTOPILOT_ENABLED',
                       'FULLY_AUTOMATED_OPERATOR_ENABLED', 'MARKET_ORDERS_ENABLED',
                       'KIS_PAPER_SESSION_ENABLED', 'KIS_PAPER_ORDER_SUBMISSION_ENABLED')) {
        [Environment]::SetEnvironmentVariable($name, 'false', 'Process')
    }
    $env:BROKER_MODE = 'mock'
    $env:DATA_MODE = 'fixture'
    [Environment]::SetEnvironmentVariable('QUANTPILOT_RUNTIME_ROLE', $null, 'Process')
    switch ($Action) {
        'Check' { & $Python -m quantpilot.jobs.check_kis_paper_connection }
        'Prepare' { & $Python -m quantpilot.jobs.prepare_kis_paper_runtime --runtime-dir $RuntimeDirectory }
        'Smoke' { & $Python -m quantpilot.jobs.run_smoke }
    }
    $resultCode = $LASTEXITCODE
} finally {
    foreach ($name in $changedNames) { [Environment]::SetEnvironmentVariable($name, $savedValues[$name], 'Process') }
    Pop-Location
}
exit $resultCode
