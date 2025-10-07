<# 
run_both_random_region.ps1 — Track loop + Discover hourly with a RANDOM region (Windows PowerShell)

What it does
- Job 1 (Discover): every DiscoverIntervalSeconds (default 3600s = 1h), pick 1 RANDOM region
  from a pool and run worker\discover_once.py in "near-now" mode (SINCE minutes).
- Job 2 (Track): run worker\track_once.py every TrackIntervalSeconds (default 30s).
- Activates venv automatically if found.
- Logs to .\logs\discover-YYYYMMDD.log and .\logs\track-YYYYMMDD.log.
- If a Python process exits with code 88 (YouTube quota exhausted), that job sleeps 1 hour.

Usage (from project root):
  # defaults: regions list below, discover=1h, track=30s, since=5m, pages=1
  .\run_both_random_region.ps1

  # custom:
  .\run_both_random_region.ps1 `
    -Regions @("US","GB","CA","AU") `
    -DiscoverIntervalSeconds 3600 `
    -TrackIntervalSeconds 30 `
    -SinceMinutes 5 `
    -MaxPages 1 `
    -Query ""

Quota NOTE
- search.list costs 100 units/page. With MaxPages=1 hourly it's fine, but adding many regions reduces daily headroom.
#>

param(
  [string[]]$Regions = @("US","GB","CA","AU","NZ","IE","SG","PH","IN","ZA"),
  [int]$DiscoverIntervalSeconds = 3600,
  [int]$TrackIntervalSeconds = 30,
  [int]$SinceMinutes = 5,
  [int]$MaxPages = 1,
  [string]$Query = ""
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
Write-Host "Discover every  = $DiscoverIntervalSeconds s (SINCE=$SinceMinutes m, MaxPages=$MaxPages, Query='$Query', RANDOM pick region)"
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

function Add-LogLine([string]$Path, [string]$Text) {
  $ts = Get-Date -Format o
  "[{0}] {1}" -f $ts, $Text | Out-File -FilePath $Path -Append -Encoding utf8
}

# --- Discover hourly (random region) ---
$discoverScript = {
  param($Regions, $Interval, $Root, $Log, $SinceMinutes, $MaxPages, $Query)
  Set-Location $Root

  while ($true) {
    # Pick one random region this cycle
    $pick = Get-Random -InputObject $Regions

    # Configure ENV for near-now
    $env:YT_RANDOM_MODE    = "0"
    $env:YT_SINCE_MODE     = "minutes"
    $env:YT_SINCE_MINUTES  = "$SinceMinutes"
    $env:YT_MAX_PAGES      = "$MaxPages"
    $env:YT_REGION         = $pick
    if ([string]::IsNullOrWhiteSpace($Query)) {
      Remove-Item Env:YT_QUERY -ErrorAction SilentlyContinue
    } else {
      $env:YT_QUERY = $Query
    }

    Add-LogLine $Log "=== DISCOVER (random region=$pick; since=${SinceMinutes}m; pages=$MaxPages; query='$Query') START ==="
    & python -u .\worker\discover_once.py *>> $Log
    $code = $LASTEXITCODE
    Add-LogLine $Log "=== DISCOVER END (code=$code) ==="

    if ($code -eq 88) {
      Add-LogLine $Log "quota exhausted -> sleeping 3600s"
      Start-Sleep -Seconds 3600
    } else {
      Start-Sleep -Seconds $Interval
    }
  }
}

# --- Track loop (every 30s by default) ---
$trackScript = {
  param($Interval, $Root, $Log)
  Set-Location $Root
  while ($true) {
    Add-LogLine $Log "=== TRACK START ==="
    & python -u .\worker\track_once.py *>> $Log
    $code = $LASTEXITCODE
    Add-LogLine $Log "=== TRACK END (code=$code) ==="

    if ($code -eq 88) {
      Add-LogLine $Log "quota exhausted -> sleeping 3600s"
      Start-Sleep -Seconds 3600
    } else {
      Start-Sleep -Seconds $Interval
    }
  }
}

# --- Start jobs ---
$discoverJob = Start-Job -Name "DiscoverRandomRegionJob" -ScriptBlock $discoverScript -ArgumentList @($Regions, $DiscoverIntervalSeconds, $ProjectRoot, $discoverLog, $SinceMinutes, $MaxPages, $Query)
$trackJob    = Start-Job -Name "TrackJob"                -ScriptBlock $trackScript    -ArgumentList @($TrackIntervalSeconds,    $ProjectRoot, $trackLog)

Write-Host ""
Write-Host "Started:"
Write-Host "  DiscoverRandomRegionJob (Id=$($discoverJob.Id))"
Write-Host "  TrackJob                 (Id=$($trackJob.Id))"
Write-Host ""
Write-Host "Tail logs:"
Write-Host "  Get-Content -Wait -Tail 30 `"$discoverLog`""
Write-Host "  Get-Content -Wait -Tail 30 `"$trackLog`""
Write-Host ""
Write-Host "Ctrl+C to stop..."

# --- Graceful shutdown ---
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
      Write-Host "DiscoverRandomRegionJob ended unexpectedly. Restarting..."
      $discoverJob = Start-Job -Name "DiscoverRandomRegionJob" -ScriptBlock $discoverScript -ArgumentList @($Regions, $DiscoverIntervalSeconds, $ProjectRoot, $discoverLog, $SinceMinutes, $MaxPages, $Query)
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
