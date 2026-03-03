param(
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Set-Location -Path $PSScriptRoot

function Invoke-CodeSign {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath
  )

  $certPfx = $env:DAVE_SIGN_CERT_PFX
  if (-not $certPfx) {
    return
  }

  $signtool = if ($env:DAVE_SIGNTOOL_PATH) { $env:DAVE_SIGNTOOL_PATH } else { "signtool.exe" }
  $timestampUrl = if ($env:DAVE_SIGN_TIMESTAMP_URL) { $env:DAVE_SIGN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
  $password = $env:DAVE_SIGN_CERT_PASS

  $args = @(
    "sign",
    "/f", $certPfx,
    "/fd", "SHA256",
    "/tr", $timestampUrl,
    "/td", "SHA256"
  )
  if ($password) {
    $args += @("/p", $password)
  }
  $args += $FilePath

  Write-Host "Code signing: $FilePath"
  & $signtool @args
  if ($LASTEXITCODE -ne 0) {
    throw "Code signing failed for $FilePath"
  }
}

function Resolve-IsccPath {
  if ($env:ISCC_PATH) {
    return $env:ISCC_PATH
  }

  $cmd = Get-Command "iscc.exe" -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }

  $candidatePaths = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
  )

  foreach ($candidate in $candidatePaths) {
    if ($candidate -and (Test-Path $candidate)) {
      return $candidate
    }
  }

  return $null
}

if (-not $SkipBuild) {
  & (Join-Path $PSScriptRoot "build_exe.ps1")
}

$releaseExe = Join-Path $PSScriptRoot "release\DAVE\DAVE.exe"
if (-not (Test-Path $releaseExe)) {
  throw "Missing release build at release\\DAVE\\DAVE.exe"
}

$runningTargets = @(
  (Join-Path $PSScriptRoot "release\\DAVE.exe"),
  $releaseExe
)
$running = Get-Process DAVE -ErrorAction SilentlyContinue | Where-Object {
  $_.Path -and $runningTargets -contains $_.Path
}
if ($running) {
  throw "Close running release DAVE executable(s) before building the installer."
}

$releaseRoot = Join-Path $PSScriptRoot "release\\DAVE"
# Avoid shipping runtime-generated logs/metrics in the installer when using -SkipBuild.
$runtimeDataDir = Join-Path $releaseRoot "data"
if (Test-Path $runtimeDataDir) {
  try {
    Remove-Item -Path $runtimeDataDir -Recurse -Force
    Write-Host "Removed runtime data directory from release package before installer build."
  } catch {
    Write-Host "Warning: Failed to remove runtime data directory '$runtimeDataDir': $($_.Exception.Message)"
  }
}

$configPath = Join-Path $PSScriptRoot "config.json"
$version = "0.3.0"
if (Test-Path $configPath) {
  try {
    $cfg = Get-Content -Path $configPath -Raw | ConvertFrom-Json
    if ($cfg.app.version) {
      $version = [string]$cfg.app.version
    }
  } catch {
    Write-Host "Using fallback installer version $version"
  }
}

$iscc = Resolve-IsccPath
$issFile = Join-Path $PSScriptRoot "installer\DAVE.iss"
if (-not (Test-Path $issFile)) {
  throw "Installer script not found: $issFile"
}
if (-not $iscc) {
  throw "Inno Setup compiler not found. Install Inno Setup or set ISCC_PATH to iscc.exe."
}

Write-Host "Building installer for DAVE version $version"
& $iscc "/DMyAppVersion=$version" $issFile
if ($LASTEXITCODE -ne 0) {
  throw "Inno Setup build failed."
}

$installerPath = Join-Path $PSScriptRoot ("release\DAVE-Setup-" + $version + ".exe")
if (-not (Test-Path $installerPath)) {
  throw "Installer output not found at $installerPath"
}
Invoke-CodeSign -FilePath $installerPath

$installerSha = (Get-FileHash -Path $installerPath -Algorithm SHA256).Hash
$shaPath = Join-Path $PSScriptRoot ("release\\DAVE-Setup-" + $version + ".sha256.txt")
Set-Content -Path $shaPath -Value $installerSha -Encoding ASCII
Write-Host "Installer SHA256: $installerSha"

Write-Host "Installer complete: $installerPath"
