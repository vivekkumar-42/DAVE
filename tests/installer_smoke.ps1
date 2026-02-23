param(
  [string]$InstallerPath = "",
  [string]$InstallDir = "C:\Program Files\DAVE"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $InstallerPath) {
  $repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
  $configPath = Join-Path $repoRoot "config.json"
  $candidate = $null
  if (Test-Path $configPath) {
    try {
      $cfg = Get-Content -Path $configPath -Raw | ConvertFrom-Json
      $version = [string]$cfg.app.version
      if ($version) {
        $byVersion = Join-Path $repoRoot ("release\\DAVE-Setup-" + $version + ".exe")
        if (Test-Path $byVersion) {
          $candidate = $byVersion
        }
      }
    } catch {
      # Ignore and fall back to directory scan.
    }
  }
  if (-not $candidate) {
    $latest = Get-ChildItem -Path (Join-Path $repoRoot "release") -Filter "DAVE-Setup-*.exe" -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 1
    if ($latest) {
      $candidate = $latest.FullName
    }
  }
  if (-not $candidate) {
    throw "No installer found under release\\DAVE-Setup-*.exe. Build it with build_installer.ps1 first."
  }
  $InstallerPath = [System.IO.Path]::GetFullPath($candidate)
}

$installDirPath = [System.IO.Path]::GetFullPath($InstallDir)
$tempRoot = Join-Path $env:TEMP "dave-installer-smoke"
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
$installLog = Join-Path $tempRoot "install.log"
$uninstallLog = Join-Path $tempRoot "uninstall.log"

if (-not (Test-Path $InstallerPath)) {
  throw "Installer not found: $InstallerPath"
}

function Invoke-SilentProcess {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [Parameter(Mandatory = $true)]
    [string[]]$Arguments
  )

  $proc = Start-Process -FilePath $FilePath -ArgumentList $Arguments -Wait -PassThru
  return $proc.ExitCode
}

function Invoke-UninstallIfPresent {
  param(
    [Parameter(Mandatory = $true)]
    [string]$TargetDir,
    [Parameter(Mandatory = $true)]
    [string]$LogPath
  )

  $uninstaller = Join-Path $TargetDir "unins000.exe"
  if (-not (Test-Path $uninstaller)) {
    return
  }

  $uninstallExit = Invoke-SilentProcess -FilePath $uninstaller -Arguments @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/LOG=$LogPath"
  )
  if ($uninstallExit -ne 0) {
    throw "Uninstall failed with exit code $uninstallExit"
  }
}

Write-Host "Installer smoke: uninstalling existing instance (if present)"
Invoke-UninstallIfPresent -TargetDir $installDirPath -LogPath $uninstallLog

Write-Host "Installer smoke: running installer"
$installExit = Invoke-SilentProcess -FilePath $InstallerPath -Arguments @(
  "/SP-",
  "/VERYSILENT",
  "/SUPPRESSMSGBOXES",
  "/NORESTART",
  # Quote values that include spaces; Inno parses /NAME="value".
  ("/DIR=""$installDirPath"""),
  "/LOG=$installLog"
)
if ($installExit -ne 0) {
  throw "Installer failed with exit code $installExit"
}

$installedExe = Join-Path $installDirPath "DAVE.exe"
if (-not (Test-Path $installedExe)) {
  throw "Installed executable not found at $installedExe"
}

Write-Host "Installer smoke: running installed --self-check"
$selfCheckExit = Invoke-SilentProcess -FilePath $installedExe -Arguments @("--self-check")
if ($selfCheckExit -ne 0) {
  throw "Installed app self-check failed with exit code $selfCheckExit"
}

Write-Host "Installer smoke: uninstalling installed instance"
Invoke-UninstallIfPresent -TargetDir $installDirPath -LogPath $uninstallLog

if (Test-Path $installedExe) {
  throw "Installed executable still present after uninstall: $installedExe"
}

Write-Host "Installer smoke passed."
Write-Host "Install log: $installLog"
Write-Host "Uninstall log: $uninstallLog"
