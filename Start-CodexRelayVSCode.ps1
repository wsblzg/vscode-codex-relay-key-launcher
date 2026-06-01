param(
  [string]$Workspace = "D:\项目",
  [string]$AccountsPath,
  [string]$Account,
  [string[]]$Accounts,
  [switch]$Next,
  [switch]$List,
  [switch]$PrepareOnly,
  [switch]$NoCopyCodexState,
  [switch]$UseSeparateExtensionsDir,
  [switch]$ShareStateByProvider,
  [string]$ProfilesRoot = (Join-Path $env:USERPROFILE ".codex-profiles"),
  [string]$ProviderStateRoot = (Join-Path $env:USERPROFILE ".codex-provider-state"),
  [string]$VSCodeUserDataRoot = (Join-Path $env:APPDATA "Code-CodexRelay"),
  [string]$VSCodeExtensionsRoot = (Join-Path $env:LOCALAPPDATA "Code-CodexRelayExtensions")
)

$ErrorActionPreference = "Stop"

function Read-JsonFile {
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    throw "File not found: $Path"
  }

  Get-Content -LiteralPath $Path -Encoding UTF8 -Raw | ConvertFrom-Json
}

function ConvertTo-JsonText {
  param([Parameter(Mandatory = $true)]$Value)

  ($Value | ConvertTo-Json -Depth 20) + "`n"
}

function Write-Utf8NoBom {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Content
  )

  $directory = Split-Path -Parent $Path
  if ($directory) {
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
  }

  $encoding = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Mask-Key {
  param([Parameter(Mandatory = $true)][string]$Key)

  if ($Key.Length -le 12) {
    return "****"
  }

  return "$($Key.Substring(0, 6))...$($Key.Substring($Key.Length - 4))"
}

function Get-ProviderName {
  param([Parameter(Mandatory = $true)]$Account)

  $provider = [string]$Account.provider
  if (-not $provider.Trim()) {
    $provider = "rightcode"
  }

  $provider = $provider.Trim()
  $provider = [regex]::Replace($provider, '[^A-Za-z0-9_.-]', '-').Trim(".-")
  if (-not $provider) {
    return "rightcode"
  }

  $provider
}

function Get-RelayData {
  if ($AccountsPath) {
    $resolvedAccountsPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($AccountsPath)
  } else {
    $relayHome = if ($env:CODEX_RELAY_HOME) { $env:CODEX_RELAY_HOME } else { Join-Path $env:USERPROFILE ".codex-relay" }
    $resolvedAccountsPath = Join-Path $relayHome "accounts.json"
  }

  $relay = Read-JsonFile -Path $resolvedAccountsPath

  if (-not $relay.accounts -or $relay.accounts.Count -eq 0) {
    throw "No relay accounts found in $resolvedAccountsPath"
  }

  [pscustomobject]@{
    Path = $resolvedAccountsPath
    Data = $relay
  }
}

function Get-AccountByName {
  param(
    [Parameter(Mandatory = $true)]$Relay,
    [Parameter(Mandatory = $true)][string]$Name
  )

  $match = @($Relay.accounts | Where-Object { $_.name -eq $Name })
  if ($match.Count -eq 0) {
    throw "Relay account not found: $Name"
  }
  if ($match.Count -gt 1) {
    throw "Duplicate relay account name: $Name"
  }

  $match[0]
}

function Get-NextAccountName {
  param([Parameter(Mandatory = $true)]$Relay)

  $names = @($Relay.accounts | ForEach-Object { $_.name })
  $preferred = [string]$Relay.preferred
  $index = [Array]::IndexOf($names, $preferred)

  if ($index -lt 0) {
    return $names[0]
  }

  $nextIndex = ($index + 1) % $names.Count
  $names[$nextIndex]
}

function Normalize-AccountNames {
  param([string[]]$Names)

  @($Names | ForEach-Object {
    $_ -split ","
  } | ForEach-Object {
    $_.Trim()
  } | Where-Object {
    $_
  })
}

function Set-TomlRootString {
  param(
    [Parameter(Mandatory = $true)][string]$Config,
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Value
  )

  $line = "$Name = `"$Value`""
  $pattern = "(?m)^$([regex]::Escape($Name))\s*=.*$"
  if ([regex]::IsMatch($Config, $pattern)) {
    return [regex]::Replace($Config, $pattern, $line)
  }

  $line + "`n" + $Config
}

function Remove-TomlSectionKey {
  param(
    [Parameter(Mandatory = $true)][string]$Config,
    [Parameter(Mandatory = $true)][string]$Section,
    [Parameter(Mandatory = $true)][string]$Name
  )

  $sectionPattern = "(?ms)(^\[$([regex]::Escape($Section))\]\s*)(.*?)(?=^\[|\z)"
  if (-not [regex]::IsMatch($Config, $sectionPattern)) {
    return $Config
  }

  [regex]::Replace(
    $Config,
    $sectionPattern,
    {
      param($match)
      $header = $match.Groups[1].Value
      $body = $match.Groups[2].Value
      $keyPattern = "(?m)^\s*$([regex]::Escape($Name))\s*=.*(?:\r?\n)?"
      $body = [regex]::Replace($body, $keyPattern, "")
      if (-not $body.Trim()) {
        return ""
      }
      return $header + $body
    }
  )
}

function Get-CodexConfigTemplate {
  param(
    [Parameter(Mandatory = $true)]$Account,
    [Parameter(Mandatory = $true)][string]$SourceConfigPath
  )

  $provider = Get-ProviderName -Account $Account

  if (Test-Path -LiteralPath $SourceConfigPath) {
    $config = Get-Content -LiteralPath $SourceConfigPath -Encoding UTF8 -Raw
  } else {
$config = @"
model = "gpt-5.5"
model_provider = "$provider"
sandbox_mode = "danger-full-access"
approval_policy = "never"
model_reasoning_effort = "high"
network_access = "enabled"
disable_response_storage = true
windows_wsl_setup_acknowledged = true
model_verbosity = "high"

[model_providers]

[model_providers.$provider]
name = "$provider"
base_url = "$($Account.baseUrl)"
wire_api = "responses"
requires_openai_auth = true
"@
  }

  if ([regex]::IsMatch($config, '(?m)^model_provider\s*=')) {
    $config = [regex]::Replace($config, '(?m)^model_provider\s*=.*$', "model_provider = `"$provider`"")
  } else {
    $config = "model_provider = `"$provider`"" + "`n" + $config
  }
  $config = Set-TomlRootString -Config $config -Name "sandbox_mode" -Value "danger-full-access"
  $config = Set-TomlRootString -Config $config -Name "approval_policy" -Value "never"
  $config = Remove-TomlSectionKey -Config $config -Section "windows" -Name "sandbox"
  $providerSectionPattern = "(?ms)(\[model_providers\.$([regex]::Escape($provider))\]\s.*?)(?=^\[|\z)"

  if ([regex]::IsMatch($config, $providerSectionPattern)) {
    $config = [regex]::Replace(
      $config,
      $providerSectionPattern,
      {
        param($match)
        $section = $match.Groups[1].Value
        if ([regex]::IsMatch($section, '(?m)^base_url\s*=')) {
          return [regex]::Replace($section, '(?m)^base_url\s*=.*$', "base_url = `"$($Account.baseUrl)`"")
        }
        return $section.TrimEnd() + "`nbase_url = `"$($Account.baseUrl)`"`n"
      }
    )
  } else {
    $config = $config.TrimEnd() + @"

[model_providers.$provider]
name = "$provider"
base_url = "$($Account.baseUrl)"
wire_api = "responses"
requires_openai_auth = true
"@
  }

  $config.TrimEnd() + "`n"
}

function Get-SharedCodexStateNames {
  @(
    "AGENTS.md",
    "history.jsonl",
    "session_index.jsonl",
    "version.json",
    "sessions",
    "archived_sessions",
    "skills",
    "prompts",
    "rules",
    "memories"
  )
}

function Ensure-ProviderStateHome {
  param(
    [Parameter(Mandatory = $true)][string]$Provider,
    [Parameter(Mandatory = $true)][string]$SourceHome
  )

  $stateHome = Join-Path $ProviderStateRoot $Provider
  [System.IO.Directory]::CreateDirectory($stateHome) | Out-Null

  foreach ($name in Get-SharedCodexStateNames) {
    $source = Join-Path $SourceHome $name
    $target = Join-Path $stateHome $name

    if (Test-Path -LiteralPath $target) {
      continue
    }

    if (Test-Path -LiteralPath $source) {
      $item = Get-Item -LiteralPath $source
      if ($item.PSIsContainer) {
        Copy-Item -LiteralPath $source -Destination $target -Recurse
      } else {
        Copy-Item -LiteralPath $source -Destination $target
      }
      continue
    }

    if ($name -match '\.') {
      Write-Utf8NoBom -Path $target -Content ""
    } else {
      [System.IO.Directory]::CreateDirectory($target) | Out-Null
    }
  }

  $stateHome
}

function Backup-ExistingStateItem {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$ProfileHome
  )

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }

  $backupRoot = Join-Path $ProfileHome ".local-state-before-provider-share"
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $backupDir = Join-Path $backupRoot $stamp
  [System.IO.Directory]::CreateDirectory($backupDir) | Out-Null

  Move-Item -LiteralPath $Path -Destination (Join-Path $backupDir (Split-Path -Leaf $Path))
}

function Link-ProviderStateItem {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Target
  )

  $sourceItem = Get-Item -LiteralPath $Source
  if ($sourceItem.PSIsContainer) {
    New-Item -ItemType Junction -Path $Target -Target $Source | Out-Null
  } else {
    New-Item -ItemType HardLink -Path $Target -Target $Source | Out-Null
  }
}

function Enable-ProviderStateSharing {
  param(
    [Parameter(Mandatory = $true)][string]$ProviderStateHome,
    [Parameter(Mandatory = $true)][string]$ProfileHome,
    [Parameter(Mandatory = $true)][string]$Provider
  )

  $markerPath = Join-Path $ProfileHome ".provider-state-links.json"
  if (Test-Path -LiteralPath $markerPath) {
    return
  }

  foreach ($name in Get-SharedCodexStateNames) {
    $source = Join-Path $ProviderStateHome $name
    $target = Join-Path $ProfileHome $name

    if (-not (Test-Path -LiteralPath $source)) {
      continue
    }

    Backup-ExistingStateItem -Path $target -ProfileHome $ProfileHome
    Link-ProviderStateItem -Source $source -Target $target
  }

  $marker = [ordered]@{
    provider = $Provider
    providerStateHome = $ProviderStateHome
    linkedAt = (Get-Date).ToString("o")
    names = @(Get-SharedCodexStateNames)
  }
  Write-Utf8NoBom -Path $markerPath -Content (ConvertTo-JsonText -Value $marker)
}

function Copy-CodexState {
  param(
    [Parameter(Mandatory = $true)][string]$SourceHome,
    [Parameter(Mandatory = $true)][string]$TargetHome
  )

  $copyNames = @(
    "AGENTS.md",
    "history.jsonl",
    "version.json",
    "skills",
    "prompts",
    "rules",
    "memories"
  )

  foreach ($name in $copyNames) {
    $source = Join-Path $SourceHome $name
    $target = Join-Path $TargetHome $name

    if (-not (Test-Path -LiteralPath $source)) {
      continue
    }

    if (Test-Path -LiteralPath $target) {
      continue
    }

    $item = Get-Item -LiteralPath $source
    if ($item.PSIsContainer) {
      Copy-Item -LiteralPath $source -Destination $target -Recurse
    } else {
      Copy-Item -LiteralPath $source -Destination $target
    }
  }
}

function Ensure-CodexProfile {
  param([Parameter(Mandatory = $true)]$Account)

  $defaultCodexHome = Join-Path $env:USERPROFILE ".codex"
  $profileHome = Join-Path $ProfilesRoot $Account.name
  $provider = Get-ProviderName -Account $Account
  [System.IO.Directory]::CreateDirectory($profileHome) | Out-Null

  $providerStateHome = $null
  if ($ShareStateByProvider) {
    $providerStateHome = Ensure-ProviderStateHome -Provider $provider -SourceHome $defaultCodexHome
    Enable-ProviderStateSharing -ProviderStateHome $providerStateHome -ProfileHome $profileHome -Provider $provider
  } elseif (-not $NoCopyCodexState) {
    Copy-CodexState -SourceHome $defaultCodexHome -TargetHome $profileHome
  }

  $auth = [ordered]@{
    OPENAI_API_KEY = $Account.apiKey
    auth_mode = "apikey"
  }
  Write-Utf8NoBom -Path (Join-Path $profileHome "auth.json") -Content (ConvertTo-JsonText -Value $auth)

  $sourceConfigPath = Join-Path $defaultCodexHome "config.toml"
  $config = Get-CodexConfigTemplate -Account $Account -SourceConfigPath $sourceConfigPath
  Write-Utf8NoBom -Path (Join-Path $profileHome "config.toml") -Content $config

  [pscustomobject]@{
    ProfileHome = $profileHome
    Provider = $provider
    ProviderStateHome = $providerStateHome
  }
}

function Start-VSCodeForAccount {
  param([Parameter(Mandatory = $true)]$Account)

  $profile = Ensure-CodexProfile -Account $Account
  $profileHome = $profile.ProfileHome
  $userDataDir = Join-Path $VSCodeUserDataRoot $Account.name
  [System.IO.Directory]::CreateDirectory($userDataDir) | Out-Null

  if ($PrepareOnly) {
    Write-Host "Prepared VSCode profile:"
    Write-Host "  account    : $($Account.name)"
    Write-Host "  key        : $(Mask-Key -Key $Account.apiKey)"
    Write-Host "  baseUrl    : $($Account.baseUrl)"
    Write-Host "  provider   : $($profile.Provider)"
    Write-Host "  CODEX_HOME : $profileHome"
    if ($profile.ProviderStateHome) {
      Write-Host "  provider state : $($profile.ProviderStateHome)"
    }
    Write-Host "  user data  : $userDataDir"
    return
  }

  $codeCommand = Get-Command code.cmd -ErrorAction SilentlyContinue
  if (-not $codeCommand) {
    $codeCommand = Get-Command code -ErrorAction SilentlyContinue
  }
  if (-not $codeCommand) {
    throw "VSCode command 'code' was not found in PATH."
  }

  if (-not (Test-Path -LiteralPath $Workspace)) {
    throw "Workspace path not found: $Workspace"
  }

  $previousCodexHome = $env:CODEX_HOME
  try {
    $env:CODEX_HOME = $profileHome

    $args = @(
      "--new-window",
      "--user-data-dir", $userDataDir
    )

    if ($UseSeparateExtensionsDir) {
      $extensionsDir = Join-Path $VSCodeExtensionsRoot $Account.name
      [System.IO.Directory]::CreateDirectory($extensionsDir) | Out-Null
      $args += @("--extensions-dir", $extensionsDir)
    }

    $args += $Workspace

    Write-Host "Starting VSCode profile:"
    Write-Host "  account    : $($Account.name)"
    Write-Host "  key        : $(Mask-Key -Key $Account.apiKey)"
    Write-Host "  baseUrl    : $($Account.baseUrl)"
    Write-Host "  provider   : $($profile.Provider)"
    Write-Host "  CODEX_HOME : $profileHome"
    if ($profile.ProviderStateHome) {
      Write-Host "  provider state : $($profile.ProviderStateHome)"
    }
    Write-Host "  user data  : $userDataDir"

    & $codeCommand.Source @args
  } finally {
    $env:CODEX_HOME = $previousCodexHome
  }
}

$relayInfo = Get-RelayData
$relay = $relayInfo.Data
Write-Host "Relay accounts from $($relayInfo.Path)"

if ($List) {
  foreach ($item in $relay.accounts) {
    $mark = if ($item.name -eq $relay.preferred) { "*" } else { " " }
    Write-Host "$mark $($item.name) $($item.baseUrl) $(Mask-Key -Key $item.apiKey)"
  }
  exit 0
}

$selectedNames = @()
if ($Accounts -and $Accounts.Count -gt 0) {
  $selectedNames = Normalize-AccountNames -Names $Accounts
} elseif ($Account) {
  $selectedNames = @($Account)
} elseif ($Next) {
  $selectedNames = @(Get-NextAccountName -Relay $relay)
} elseif ($relay.preferred) {
  $selectedNames = @([string]$relay.preferred)
} else {
  $selectedNames = @($relay.accounts[0].name)
}

foreach ($name in $selectedNames) {
  $accountObject = Get-AccountByName -Relay $relay -Name $name
  Start-VSCodeForAccount -Account $accountObject
}