<# 
run_both.ps1 — run discover & track together (Windows PowerShell)

What it does
- Runs discover_once.py (one-shot or loop on an interval).
- Runs track_once.py in a 30-second loop (default), so snapshots are captured promptly.
- Writes logs to .\logs\discover-YYYYMMDD.log and .\logs\track-YYYYMMDD.log.
- Activates venv automatically if found.

Usage (from project root):
  # run discover once + track loop every 30s
  .\run_both.ps1 -DiscoverOnce

  # or: run both in loops (discover each 10 minutes, track each 30s)
  .\run_both.ps1 -DiscoverIntervalSeconds 600 -TrackIntervalSeconds 30

  # see logs live (in another terminal):
  Get-Content -Wait -Tail 30 .\logs\discover-$(Get-Date -UFormat %Y%m%d).log
  Get-Content -Wait -Tail 30 .\logs\track-$(Get-Date -UFormat %Y%m%d).log

Notes
- If YT_API_KEY/MONGO_URI are not set in the environment, the Python scripts
  will fallback to .env (because they use python-dotenv with override=False).
- Exit code 88 from the Python scripts is treated as "quota exhausted":
  the loop sleeps longer (1 hour) before retrying.
#>

param(
  [int]$DiscoverIntervalSeconds = 600,   # 10 minutes; use -DiscoverOnce to run only once
  [int]$TrackIntervalSeconds = 30,
  [switch]$DiscoverOnce
)

$ErrorActionPreference = "Stop"

# --- Resolve project root & logs ---
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$logsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
$discoverLog = Join-Path $logsDir ("discover-{0}.log" -f (Get-Date -UFormat %Y%m%d))
$trackLog    = Join-Path $logsDir ("track-{0}.log"    -f (Get-Date -UFormat %Y%m%d))

Write-Host "ProjectRoot     = $ProjectRoot"
Write-Host "Discover every  = " ($DiscoverOnce ? "ONCE" : "$DiscoverIntervalSeconds s")
Write-Host "Track every     = $TrackIntervalSeconds s"
Write-Host "Logs:"
Write-Host "  $discoverLog"
Write-Host "  $trackLog"

# --- Activate venv if present ---
$venvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
  Write-Host "Activating venv..."
  . $venvActivate
} else {
  Write-Host "venv not found, using system Python."
}

# --- Helpers ---
function Add-LogLine([string]$Path, [string]$Text) {
  $ts = Get-Date -Format o
  "[{0}] {1}" -f $ts, $Text | Out-File -FilePath $Path -Append -Encoding utf8
}

# --- Discover loop (or once) ---
$discoverScript = {
  param($Interval, $Root, $Log, $Once)
  Set-Location $Root
  while ($true) {
    Add-LogLine $Log "=== RUN START ==="
    & python -u .\worker\discover_once.py *>> $Log
    $code = $LASTEXITCODE
    Add-LogLine $Log "=== RUN END (code=$code) ==="

    if ($Once) { break }

    if ($code -eq 88) {
      Add-LogLine $Log "quota exhausted -> sleeping 3600s"
      Start-Sleep -Seconds 3600
    } else {
      Start-Sleep -Seconds $Interval
    }
  }
}

# --- Track loop (always loop) ---
$trackScript = {
  param($Interval, $Root, $Log)
  Set-Location $Root
  while ($true) {
    Add-LogLine $Log "=== RUN START ==="
    & python -u .\worker\track_once.py *>> $Log
    $code = $LASTEXITCODE
    Add-LogLine $Log "=== RUN END (code=$code) ==="

    if ($code -eq 88) {
      Add-LogLine $Log "quota exhausted -> sleeping 3600s"
      Start-Sleep -Seconds 3600
    } else {
      Start-Sleep -Seconds $Interval
    }
  }
}

# --- Start jobs ---
$discoverJob = Start-Job -Name "DiscoverJob" -ScriptBlock $discoverScript -ArgumentList @($DiscoverIntervalSeconds, $ProjectRoot, $discoverLog, [bool]$DiscoverOnce)
$trackJob    = Start-Job -Name "TrackJob"    -ScriptBlock $trackScript    -ArgumentList @($TrackIntervalSeconds,    $ProjectRoot, $trackLog)

Write-Host ""
Write-Host "Started:"
Write-Host "  DiscoverJob (Id=$($discoverJob.Id))"
Write-Host "  TrackJob    (Id=$($trackJob.Id))"
Write-Host ""
Write-Host "Tip: tail logs in another terminal:"
Write-Host "  Get-Content -Wait -Tail 30 `"$discoverLog`""
Write-Host "  Get-Content -Wait -Tail 30 `"$trackLog`""
Write-Host ""
Write-Host "Press Ctrl+C to stop both jobs..."

# --- Graceful shutdown on Ctrl+C ---
$stop = $false
$handler = {
  Write-Host "`nStopping jobs..."
  $script:stop = $true
}
$null = Register-EngineEvent -SourceIdentifier Console_CancelKeyPress -Action $handler

try {
  while (-not $stop) {
    Start-Sleep -Seconds 1
    if ((Get-Job -Id $discoverJob.Id -ErrorAction SilentlyContinue).State -in 'Failed','Completed') {
      Receive-Job -Id $discoverJob.Id -ErrorAction SilentlyContinue | Out-Null
      # If it was "once", that's expected; otherwise, restart it.
      if (-not $DiscoverOnce) {
        Write-Host "DiscoverJob ended unexpectedly. Restarting..."
        $discoverJob = Start-Job -Name "DiscoverJob" -ScriptBlock $discoverScript -ArgumentList @($DiscoverIntervalSeconds, $ProjectRoot, $discoverLog, [bool]$DiscoverOnce)
      }
    }
    if ((Get-Job -Id $trackJob.Id -ErrorAction SilentlyContinue).State -in 'Failed','Completed') {
      Receive-Job -Id $trackJob.Id -ErrorAction SilentlyContinue | Out-Null
      Write-Host "TrackJob ended unexpectedly. Restarting..."
      $trackJob = Start-Job -Name "TrackJob" -ScriptBlock $trackScript -ArgumentList @($TrackIntervalSeconds, $ProjectRoot, $trackLog)
    }
  }
}
finally {
  Unregister-Event -SourceIdentifier Console_CancelKeyPress -ErrorAction SilentlyContinue
  Get-Job | Stop-Job -Force -ErrorAction SilentlyContinue
  Get-Job | Remove-Job -Force -ErrorAction SilentlyContinue
  Write-Host "Stopped."
}
