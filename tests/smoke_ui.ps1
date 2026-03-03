param(
  [string]$ExePath = "",
  [int]$AutoExitSeconds = 5,
  [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($AutoExitSeconds -lt 0) {
  throw "AutoExitSeconds must be >= 0."
}
if ($TimeoutSeconds -lt 1) {
  throw "TimeoutSeconds must be >= 1."
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
    # Ignore; may be a console process with no main window.
  }

  Start-Sleep -Milliseconds 700

  if (-not $Process.HasExited) {
    try {
      $Process.Kill($true)
    } catch {
      # Ignore; process may have exited concurrently.
    }
  }
}

$proc = $null
try {
  if ($ExePath) {
    if (-not (Test-Path $ExePath)) {
      throw "Executable not found: $ExePath"
    }

    $fullExePath = [System.IO.Path]::GetFullPath($ExePath)
    Write-Host "Running UI smoke against: $fullExePath"
    $proc = Start-Process -FilePath $fullExePath -ArgumentList @("--auto-exit-seconds=$AutoExitSeconds") -PassThru
  } else {
    $repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    Write-Host "Running UI smoke against source app from: $repoRoot"
    $proc = Start-Process -FilePath "py" -ArgumentList @("-3", "main.py", "--auto-exit-seconds=$AutoExitSeconds") -WorkingDirectory $repoRoot -PassThru
  }

  if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
    throw "UI smoke test timed out after $TimeoutSeconds seconds."
  }
  if ($proc.ExitCode -ne 0) {
    throw "UI smoke failed with exit code $($proc.ExitCode)."
  }

  Write-Host "UI smoke passed."
}
finally {
  if ($proc -ne $null) {
    Stop-ProcessSafely -Process $proc
  }
}
