param(
  [string]$ExePath = ".\release\DAVE\DAVE.exe",
  [int]$TimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not (Test-Path $ExePath)) {
  throw "Release executable not found: $ExePath"
}

Write-Host "Running release smoke test via --self-check"
$proc = Start-Process -FilePath $ExePath -ArgumentList "--self-check" -PassThru
if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
  try { $proc.Kill() } catch {}
  throw "Smoke test timed out after $TimeoutSeconds seconds."
}
if ($proc.ExitCode -ne 0) {
  throw "Smoke test failed with exit code $($proc.ExitCode)."
}

Write-Host "Smoke test passed."
