$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$scriptPath = Join-Path $projectRoot "Start-CodexRelayVSCode.ps1"
$testRoot = Join-Path $env:TEMP ("codex-relay-tests-" + [guid]::NewGuid().ToString("N"))
$relayHome = Join-Path $testRoot "relay"
$customRelayHome = Join-Path $testRoot "custom-relay"
$profilesRoot = Join-Path $testRoot "profiles"
$providerStateRoot = Join-Path $testRoot "provider-state"
$workspace = Join-Path $testRoot "workspace"

function Assert-True {
  param(
    [Parameter(Mandatory = $true)][bool]$Condition,
    [Parameter(Mandatory = $true)][string]$Message
  )

  if (-not $Condition) {
    throw $Message
  }
}

try {
  New-Item -ItemType Directory -Path $relayHome, $customRelayHome, $profilesRoot, $providerStateRoot, $workspace | Out-Null

  $accounts = @{
    preferred = "acct-a"
    accounts = @(
      @{
        name = "acct-a"
        provider = "rightcode"
        baseUrl = "https://api.example.test"
        apiKey = "sk-test-aaaaaaaaaaaaaaaa"
      }
    )
  }

  $accountsJson = ($accounts | ConvertTo-Json -Depth 10) + "`n"
  $accountsPath = Join-Path $customRelayHome "accounts.json"
  [System.IO.File]::WriteAllText(
    $accountsPath,
    $accountsJson,
    (New-Object System.Text.UTF8Encoding($false))
  )

  $previousRelayHome = $env:CODEX_RELAY_HOME
  $env:CODEX_RELAY_HOME = $relayHome

  $output = & powershell -NoProfile -ExecutionPolicy Bypass -File $scriptPath `
    -AccountsPath $accountsPath `
    -Account "acct-a" `
    -Workspace $workspace `
    -ProfilesRoot $profilesRoot `
    -ProviderStateRoot $providerStateRoot `
    -ShareStateByProvider `
    -PrepareOnly 2>&1

  Assert-True ($LASTEXITCODE -eq 0) "PrepareOnly failed: $output"

  $profileHome = Join-Path $profilesRoot "acct-a"
  $stateHome = Join-Path $providerStateRoot "rightcode"
  $markerPath = Join-Path $profileHome ".provider-state-links.json"
  $authPath = Join-Path $profileHome "auth.json"
  $configPath = Join-Path $profileHome "config.toml"

  Assert-True (Test-Path -LiteralPath $stateHome) "Provider state home was not created."
  Assert-True (Test-Path -LiteralPath $markerPath) "Provider-state marker was not created."
  Assert-True (Test-Path -LiteralPath $authPath) "Account auth.json was not created."
  Assert-True (Test-Path -LiteralPath $configPath) "Account config.toml was not created."
  Assert-True ((Get-Content -LiteralPath $configPath -Encoding UTF8 -Raw) -match 'model_provider\s*=\s*"rightcode"') "Provider name was not written to config.toml."
  $outputText = ($output | Out-String)
  Assert-True ($outputText -match 'provider state') "PrepareOnly output did not show provider state path."
  Assert-True ($outputText -match [regex]::Escape($accountsPath)) "PrepareOnly output did not show the selected accounts.json path."

  Write-Host "Start-CodexRelayVSCode shared provider state test passed."
} finally {
  $env:CODEX_RELAY_HOME = $previousRelayHome
  if (Test-Path -LiteralPath $testRoot) {
    Remove-Item -LiteralPath $testRoot -Recurse -Force
  }
}