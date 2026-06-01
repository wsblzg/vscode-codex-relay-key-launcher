param(
  [string]$ProfilesRoot = (Join-Path $env:USERPROFILE ".codex-profiles")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ProfilesRoot)) {
  Write-Host "No profile root found: $ProfilesRoot"
  exit 0
}

Get-ChildItem -LiteralPath $ProfilesRoot -Directory | ForEach-Object {
  $authPath = Join-Path $_.FullName "auth.json"
  $configPath = Join-Path $_.FullName "config.toml"
  $key = ""

  if (Test-Path -LiteralPath $authPath) {
    $auth = Get-Content -LiteralPath $authPath -Encoding UTF8 -Raw | ConvertFrom-Json
    $rawKey = [string]$auth.OPENAI_API_KEY
    if ($rawKey.Length -gt 12) {
      $key = "$($rawKey.Substring(0, 6))...$($rawKey.Substring($rawKey.Length - 4))"
    } else {
      $key = "****"
    }
  }

  [pscustomobject]@{
    Name = $_.Name
    Path = $_.FullName
    HasAuth = Test-Path -LiteralPath $authPath
    HasConfig = Test-Path -LiteralPath $configPath
    Key = $key
  }
} | Format-Table -AutoSize
