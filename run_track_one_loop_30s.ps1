<#
  run_track_once.ps1 - One-shot / Loop runner for worker/track_once.py
  Stop codes (one-shot OR loop terminate case):
    0  = success (only meaningful in one-shot)
    88 = quota exhausted (one-shot: exit; loop: sleep cooldown & continue)
    <>0,88 = error (exit with same code)
#>

[CmdletBinding()]
param(
  [switch]$Loop = $false,              # Run repeatedly if set
  [int]$IntervalSeconds = 30,          # Sleep between successful runs (Loop only)
  [int]$CooldownSeconds = 900,         # Sleep when hit quota (code 88) then continue (Loop only)
  [string]$PythonExe = "python"        # Or "py"
)

# =======================
# Paths
# =======================
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = $ScriptDir
$WorkerDir = Join-Path $RepoRoot "worker"
$TrackPath = Join-Path $WorkerDir "track_once.py"

# =======================
# Logging
# =======================
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

function Get-LogFile {
  $date = Get-Date -Format "yyyy-MM-dd"
  Join-Path $LogDir "track-$date.log"
}
function Write-Log([string]$msg, [string]$level="INFO") {
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  $line = "[$ts] [$level] $msg"
  Write-Host $line
  $line | Out-File -FilePath (Get-LogFile) -Append -Encoding utf8
}

try {
  $TranscriptPath = Join-Path $LogDir ("transcript-track-" + (Get-Date -Format "yyyy-MM-dd") + ".log")
  if (-not (Test-Path $TranscriptPath)) { New-Item -ItemType File -Path $TranscriptPath | Out-Null }
  Start-Transcript -Path $TranscriptPath -Append | Out-Null
} catch { Write-Log "Start-Transcript failed: $($_.Exception.Message)" "WARN" }

# =======================
# Load .env (NO hardcoding)
# =======================
function Load-DotEnv([string]$FilePath) {
  if (-not (Test-Path $FilePath)) { return }
  $lines = Get-Content -Raw -Path $FilePath -Encoding UTF8 -ErrorAction SilentlyContinue -ReadCount 0
  foreach ($line in ($lines -split "`r?`n")) {
    if ($line -match '^\s*#' -or $line -match '^\s*$') { continue }
    $parts = $line -split '=', 2
    if ($parts.Count -lt 2) { continue }
    $k = $parts[0].Trim()
    $v = $parts[1].Trim()
    if ($v.StartsWith('"') -and $v.EndsWith('"')) { $v = $v.Substring(1, $v.Length-2) }
    elseif ($v.StartsWith("'") -and $v.EndsWith("'")) { $v = $v.Substring(1, $v.Length-2) }
    ${env:$k} = $v
  }
}
$DotEnvPath = Join-Path $RepoRoot ".env"
Load-DotEnv -FilePath $DotEnvPath

# Masked key logs (no secrets)
if ($env:YT_API_KEY) {
  $head = $env:YT_API_KEY.Substring(0, [Math]::Min(6, $env:YT_API_KEY.Length))
  $tail = $env:YT_API_KEY.Substring([Math]::Max(0, $env:YT_API_KEY.Length-4))
  Write-Log ("YT_API_KEY loaded (masked): {0}...{1}" -f $head, $tail)
} else {
  Write-Log "YT_API_KEY is empty or not required for tracker." "WARN"
}
if ($env:MONGO_URI) { Write-Log "MONGO_URI loaded from .env" }
else { Write-Log "MONGO_URI is empty. Please set MONGO_URI=mongodb://..." "WARN" }

# =======================
# Helper: invoke once
# =======================
function Invoke-TrackOnce {
  if (-not (Test-Path $TrackPath)) {
    Write-Log "Script not found: $TrackPath" "ERROR"
    return 2
  }
  Write-Log "Running track_once ($TrackPath)"
  Push-Location $RepoRoot
  & $PythonExe $TrackPath
  $code = $LASTEXITCODE
  Pop-Location
  Write-Log ("track_once exit code = {0}" -f $code)
  return $code
}

# =======================
# One-shot or Loop
# =======================
if (-not $Loop) {
  $code = Invoke-TrackOnce
  if ($code -eq 0) {
    Write-Log "track_once completed successfully."
  } elseif ($code -eq 88) {
    Write-Log "Quota exhausted (88). Exiting now with 88." "WARN"
  } else {
    Write-Log ("track_once failed with exit code {0}." -f $code) "ERROR"
  }
  try { Stop-Transcript | Out-Null } catch {}
  exit $code
}

# Loop mode
Write-Log ("Loop mode enabled. Interval={0}s, CooldownOnQuota={1}s" -f $IntervalSeconds, $CooldownSeconds)
while ($true) {
  $code = Invoke-TrackOnce

  if ($code -eq 0) {
    Write-Log ("Success. Sleeping {0}s before next run..." -f $IntervalSeconds)
    Start-Sleep -Seconds $IntervalSeconds
    continue
  }
  elseif ($code -eq 88) {
    Write-Log ("Quota exhausted. Cooling down {0}s, then continue..." -f $CooldownSeconds) "WARN"
    Start-Sleep -Seconds $CooldownSeconds
    continue
  }
  else {
    Write-Log ("Error exit {0}. Stopping loop and exiting." -f $code) "ERROR"
    try { Stop-Transcript | Out-Null } catch {}
    exit $code
  }
}