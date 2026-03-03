param(
  [string]$ExePath = ".\release\DAVE\DAVE.exe",
  [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not (Test-Path $ExePath)) {
  throw "Release executable not found: $ExePath"
}

function Stop-ProcessSafely {
  param(
    [Parameter(Mandatory = $true)]
    [System.Diagnostics.Process]$Process
  )

  if ($Process.HasExited) {
    return
  }

  try {
    $null = $Process.CloseMainWindow()
  } catch {
    # Ignore; this may be a console process with no window.
  }
  Start-Sleep -Milliseconds 500

  if (-not $Process.HasExited) {
    try { $Process.Kill($true) } catch {}
  }
}

Write-Host "Running release smoke test via --self-check"
$proc = Start-Process -FilePath $ExePath -ArgumentList "--self-check" -PassThru
try {
  if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
    throw "Smoke test timed out after $TimeoutSeconds seconds."
  }
  if ($proc.ExitCode -ne 0) {
    throw "Smoke test failed with exit code $($proc.ExitCode)."
  }
}
finally {
  Stop-ProcessSafely -Process $proc
}

Write-Host "Smoke test passed."
