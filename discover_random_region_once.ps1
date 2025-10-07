<# 
discover_random_region_once.ps1 — One-shot discover using a RANDOM region from a pool.

Use case:
- Schedule this script hourly (Task Scheduler) to scan "near-now" videos in a random region.
- Track can run as a separate long-lived loop (e.g., run_both.ps1 or your own track loop).

Params:
  -Regions           string[]  Default: US,GB,CA,AU,NZ,IE,SG,PH,IN,ZA
  -SinceMinutes      int       Default: 5   (near-now window)
  -MaxPages          int       Default: 1   (100 quota units/page)
  -Query             string    Default: ""  (empty = broad)
  -ProjectRoot       string    Default: script folder
Behavior:
  - Sets YT_RANDOM_MODE=0 and SINCE=minutes (near-now).
  - Picks a random region from -Regions, sets YT_REGION, runs worker\discover_once.py exactly once.
  - Logs to .\logs\discover-YYYYMMDD.log

Example:
  .\discover_random_region_once.ps1
  .\discover_random_region_once.ps1 -Regions @("US","GB","CA") -SinceMinutes 10 -MaxPages 1 -Query "gaming"
#>

param(
  [string[]]$Regions = @("US","GB","CA","AU","NZ","IE","SG","PH","IN","ZA"),
  [int]$SinceMinutes = 5,
  [int]$MaxPages = 1,
  [string]$Query = "",
  [string]$ProjectRoot = $(Split-Path -Parent $MyInvocation.MyCommand.Path)
)

$ErrorActionPreference = "Stop"

Set-Location $ProjectRoot

# Logs
$logsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
$discoverLog = Join-Path $logsDir ("discover-{0}.log" -f (Get-Date -UFormat %Y%m%d))

# venv if available
$venvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) { . $venvActivate }

# Configure near-now
$env:YT_RANDOM_MODE = "0"
$env:YT_SINCE_MODE = "minutes"
$env:YT_SINCE_MINUTES = "$SinceMinutes"
$env:YT_MAX_PAGES = "$MaxPages"

if ([string]::IsNullOrWhiteSpace($Query)) {
  Remove-Item Env:YT_QUERY -ErrorAction SilentlyContinue
} else {
  $env:YT_QUERY = $Query
}

# Pick a random region
$pick = Get-Random -InputObject $Regions
$env:YT_REGION = $pick

# Log header
$ts = Get-Date -Format o
"[{0}] === RANDOM DISCOVER (region={1}, since={2}m, pages={3}, query='{4}') ===" -f $ts, $pick, $SinceMinutes, $MaxPages, $Query | `
  Out-File -FilePath $discoverLog -Append -Encoding utf8

# Run discover once
& python -u .\worker\discover_once.py *>> $discoverLog
$code = $LASTEXITCODE
$ts = Get-Date -Format o
"[{0}] === END (code={1}) ===" -f $ts, $code | Out-File -FilePath $discoverLog -Append -Encoding utf8

exit $code
