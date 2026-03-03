param(
  [switch]$OneFile
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Set-Location -Path $PSScriptRoot
$releaseDir = Join-Path $PSScriptRoot "release"
$buildDir = Join-Path $PSScriptRoot "build"
$isOneFile = [bool]$OneFile
$distRoot = if ($isOneFile) { $releaseDir } else { Join-Path $releaseDir "DAVE" }
$exePath = if ($isOneFile) { Join-Path $releaseDir "DAVE.exe" } else { Join-Path $distRoot "DAVE.exe" }
$specPath = Join-Path $PSScriptRoot "DAVE.spec"

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

New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
if (-not $isOneFile) {
  New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
}

$runningTargets = @(
  (Join-Path $releaseDir "DAVE.exe"),
  (Join-Path $releaseDir "DAVE\\DAVE.exe")
)
$running = Get-Process DAVE -ErrorAction SilentlyContinue | Where-Object {
  $_.Path -and $runningTargets -contains $_.Path
}
if ($running) {
  throw "Close running release DAVE executable(s) before building."
}

Write-Host "Installing runtime dependencies..."
py -3 -m pip install -r requirements.txt

if (Test-Path $specPath) {
  Remove-Item -Path $specPath -Force
}

$bundleMode = if ($isOneFile) { "onefile" } else { "onedir (fast startup)" }
Write-Host "Building DAVE.exe with PyInstaller ($bundleMode)..."
$pyInstallerArgs = @(
  "--noconfirm",
  "--clean",
  "--name",
  "DAVE",
  "--distpath",
  $releaseDir,
  "--workpath",
  $buildDir,
  "--specpath",
  $PSScriptRoot,
  "--windowed",
  "--disable-windowed-traceback",
  "--collect-submodules",
  "app.ui",
  "--collect-submodules",
  "app.modules",
  "--hidden-import",
  "customtkinter",
  "--hidden-import",
  "groq",
  "--hidden-import",
  "google.genai",
  "--collect-all",
  "customtkinter"
)
foreach ($pkg in @("sklearn", "scipy", "joblib", "threadpoolctl")) {
  $pyInstallerArgs += "--collect-all"
  $pyInstallerArgs += $pkg
}
if ($isOneFile) {
  $pyInstallerArgs += "--onefile"
}
py -3 -m PyInstaller @pyInstallerArgs main.py

if (-not (Test-Path $exePath)) {
  throw "Build failed: expected executable not found at $exePath"
}
Invoke-CodeSign -FilePath $exePath

if (Test-Path (Join-Path $PSScriptRoot "config.json")) {
  Copy-Item -Path (Join-Path $PSScriptRoot "config.json") -Destination $distRoot -Force
}
if (Test-Path (Join-Path $PSScriptRoot "config.template.json")) {
  Copy-Item -Path (Join-Path $PSScriptRoot "config.template.json") -Destination $distRoot -Force
}
if (Test-Path (Join-Path $PSScriptRoot "README.md")) {
  Copy-Item -Path (Join-Path $PSScriptRoot "README.md") -Destination $distRoot -Force
}
if (Test-Path (Join-Path $PSScriptRoot "CHANGELOG.md")) {
  Copy-Item -Path (Join-Path $PSScriptRoot "CHANGELOG.md") -Destination $distRoot -Force
}

$sha = (Get-FileHash -Path $exePath -Algorithm SHA256).Hash
Set-Content -Path (Join-Path $releaseDir "DAVE.sha256.txt") -Value $sha -Encoding ASCII

$version = "0.3.0"
$channel = "stable"
try {
  $configPath = Join-Path $PSScriptRoot "config.json"
  if (Test-Path $configPath) {
    $cfg = Get-Content -Path $configPath -Raw | ConvertFrom-Json
    if ($cfg.app.version) {
      $version = [string]$cfg.app.version
    }
  }
} catch {
  Write-Host "Manifest version fallback to $version"
}

$artifactPath = if ($isOneFile) { "DAVE.exe" } else { "DAVE\\DAVE.exe" }
$manifest = [ordered]@{
  channel = $channel
  version = $version
  built_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  artifact = $artifactPath
  sha256 = $sha
  download_url = ""
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $releaseDir "channel-stable.json") -Encoding UTF8

Write-Host "Build complete: $exePath"
Write-Host "Release package: $distRoot"
