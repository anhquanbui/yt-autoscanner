<#
  run_track_one_loop_30s.ps1 (v3)
  - BOM-safe, inline-comment-safe .env loader with optional debug
  - Loop runner for worker/track_once.py
#>

[CmdletBinding()]
param(
  [switch]$Loop = $true,              # Continuous loop by default
  [int]$IntervalSeconds = 30,         # Delay after success
  [int]$CooldownSeconds = 900,        # Delay after quota (88)
  [string]$PythonExe = "python",      # Or "py"
  [switch]$DebugEnv = $false          # Print parsed values (masked) for debugging
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
# Helpers
# =======================
function Remove-BOM([string]$s) {
  if ([string]::IsNullOrEmpty($s)) { return $s }
  if ($s[0] -eq [char]0xFEFF) { return $s.Substring(1) }
  return $s
}

# Return (key,value) or $null if not parseable
function Parse-EnvLine([string]$line) {
  $line = Remove-BOM($line)

  # Ignore comments/blank
  if ($line -match '^\s*#' -or $line -match '^\s*$') { return $null }

  # Trim "export " prefix
  $clean = $line -replace '^\s*export\s+', ''

  # Regex: KEY = VALUE (captures whole VALUE including spaces)
  $m = [regex]::Match($clean, '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$')
  if (-not $m.Success) { return $null }
  $k = $m.Groups[1].Value

  $v = $m.Groups[2].Value

  # Strip inline comments if value is NOT quoted
  $isDoubleQuoted = ($v.StartsWith('"') -and $v.TrimEnd().EndsWith('"'))
  $isSingleQuoted = ($v.StartsWith("'") -and $v.TrimEnd().EndsWith("'"))
  if (-not ($isDoubleQuoted -or $isSingleQuoted)) {
    # Remove inline comments starting with space+# or space+; or trailing commas
    $v = ($v -split '\s+#',2)[0]
    $v = ($v -split '\s+;',2)[0]
    $v = $v.Trim()
  } else {
    $v = $v.Trim()
  }

  # Remove wrapping quotes
  if ($v.StartsWith('"') -and $v.EndsWith('"')) { $v = $v.Substring(1, $v.Length-2) }
  elseif ($v.StartsWith("'") -and $v.EndsWith("'")) { $v = $v.Substring(1, $v.Length-2) }

  return @($k, $v)
}

# =======================
# Load .env
# =======================
function Load-DotEnv([string]$FilePath, [switch]$Debug=$false) {
  if (-not (Test-Path $FilePath)) {
    Write-Log ".env file not found at $FilePath" "WARN"
    return
  }
  try {
    $raw = Get-Content -Path $FilePath -Encoding UTF8
    $loaded = @()
    foreach ($line in $raw) {
      $parsed = Parse-EnvLine $line
      if ($null -eq $parsed) { continue }
      $k = $parsed[0]
      $v = $parsed[1]

      ${env:$k} = $v
      $loaded += $k

      if ($Debug) {
        $mask = if ($v.Length -ge 10) { "{0}...{1}" -f $v.Substring(0,6), $v.Substring($v.Length-4) }
                elseif ($v.Length -gt 0) { "(len={0})" -f $v.Length }
                else { "(empty)" }
        Write-Log ("  · loaded {0} = {1}" -f $k, $mask)
      }
    }
    Write-Log (".env loaded from {0}. Keys: {1}" -f $FilePath, ($loaded -join ", "))
  }
  catch {
    Write-Log ("Failed to load .env: {0}" -f $_.Exception.Message) "ERROR"
  }
}

$DotEnvPath = Join-Path $RepoRoot ".env"
Load-DotEnv -FilePath $DotEnvPath -Debug:$DebugEnv

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
# Invoke once (clean exit code)
# =======================
function Invoke-TrackOnce {
  if (-not (Test-Path $TrackPath)) {
    Write-Log "Script not found: $TrackPath" "ERROR"
    return 2
  }
  Write-Log "Running track_once ($TrackPath)"
  Push-Location $RepoRoot
  & $PythonExe $TrackPath 2>&1 | Out-Host
  $code = $LASTEXITCODE
  Pop-Location
  Write-Log ("track_once exit code = {0}" -f $code)
  return [int]$code
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
